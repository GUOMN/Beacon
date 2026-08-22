import CoreBluetooth
import Foundation

final class BluetoothManager: NSObject, ObservableObject {
    @Published private(set) var linkState: BluetoothLinkState = .unavailable
    @Published private(set) var logLines: [String] = []

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var controlCharacteristic: CBCharacteristic?
    private var heartbeatTimer: Timer?
    private var sequence: UInt8 = 0
    private var latestSnapshot: Data?

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }

    deinit {
        heartbeatTimer?.invalidate()
    }

    func startScanning() {
        guard central.state == .poweredOn else {
            linkState = .unavailable
            appendLog("系统蓝牙尚未开启")
            return
        }
        linkState = .scanning
        appendLog("搜索 \(BLEProtocol.deviceName)")
        central.scanForPeripherals(
            withServices: [CBUUID(string: BLEProtocol.serviceUUID)],
            options: [CBCentralManagerScanOptionAllowDuplicatesKey: false]
        )
    }

    func disconnect() {
        guard let peripheral else { return }
        central.cancelPeripheralConnection(peripheral)
    }

    func sendSnapshot(remaining: UInt8, periodUsed: UInt8, tasks: [TaskSlot]) {
        sequence &+= 1
        let data = BLEProtocol.snapshot(
            sequence: sequence,
            remaining: remaining,
            periodUsed: periodUsed,
            tasks: tasks
        )
        latestSnapshot = data
        write(data)
    }

    private func write(_ data: Data) {
        guard let peripheral, let characteristic = controlCharacteristic else { return }
        let type: CBCharacteristicWriteType =
            characteristic.properties.contains(.write) ? .withResponse : .withoutResponse
        peripheral.writeValue(data, for: characteristic, type: type)
    }

    private func beginHeartbeat() {
        heartbeatTimer?.invalidate()
        heartbeatTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.sequence &+= 1
            self.write(BLEProtocol.heartbeat(sequence: self.sequence))
        }
    }

    private func appendLog(_ message: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        logLines.append("\(formatter.string(from: Date()))  \(message)")
        if logLines.count > 100 {
            logLines.removeFirst(logLines.count - 100)
        }
    }
}

extension BluetoothManager: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn {
            startScanning()
        } else {
            heartbeatTimer?.invalidate()
            linkState = .unavailable
            appendLog("系统蓝牙不可用：\(central.state.rawValue)")
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any],
                        rssi RSSI: NSNumber) {
        let advertisedName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
        guard advertisedName == BLEProtocol.deviceName || peripheral.name == BLEProtocol.deviceName else {
            return
        }

        central.stopScan()
        self.peripheral = peripheral
        peripheral.delegate = self
        linkState = .connecting
        appendLog("发现灯板，正在连接，信号 \(RSSI) dBm")
        central.connect(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        appendLog("蓝牙连接成功，正在发现服务")
        peripheral.discoverServices([CBUUID(string: BLEProtocol.serviceUUID)])
    }

    func centralManager(_ central: CBCentralManager,
                        didFailToConnect peripheral: CBPeripheral,
                        error: Error?) {
        linkState = .disconnected
        appendLog("连接失败：\(error?.localizedDescription ?? "未知原因")")
        startScanning()
    }

    func centralManager(_ central: CBCentralManager,
                        didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?) {
        heartbeatTimer?.invalidate()
        controlCharacteristic = nil
        linkState = .disconnected
        appendLog("连接断开，开始自动重连")
        startScanning()
    }
}

extension BluetoothManager: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error {
            appendLog("发现服务失败：\(error.localizedDescription)")
            return
        }
        peripheral.services?.forEach { peripheral.discoverCharacteristics(
            [CBUUID(string: BLEProtocol.controlUUID)], for: $0
        ) }
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didDiscoverCharacteristicsFor service: CBService,
                    error: Error?) {
        if let error {
            appendLog("发现控制特征失败：\(error.localizedDescription)")
            return
        }
        guard let characteristic = service.characteristics?.first(where: {
            $0.uuid == CBUUID(string: BLEProtocol.controlUUID)
        }) else {
            appendLog("没有找到灯板控制特征")
            return
        }

        controlCharacteristic = characteristic
        linkState = .connected
        appendLog("灯板控制通道已就绪")
        beginHeartbeat()
        if let latestSnapshot { write(latestSnapshot) }
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didWriteValueFor characteristic: CBCharacteristic,
                    error: Error?) {
        if let error {
            appendLog("发送失败：\(error.localizedDescription)")
        }
    }
}
