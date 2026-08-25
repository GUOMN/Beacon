import CoreBluetooth
import Darwin
import Foundation

private let beaconPrefix = "CODEX-LIGHT-"
private let beaconService = CBUUID(string: "0100C310-7625-819E-934C-32B8E4177D6A")
private let controlCharacteristic = CBUUID(string: "0200C310-7625-819E-934C-32B8E4177D6A")
private let otaCharacteristic = CBUUID(string: "0300C310-7625-819E-934C-32B8E4177D6A")

private func stateName(_ state: CBManagerState) -> String {
    switch state {
    case .unknown: return "unknown"
    case .resetting: return "resetting"
    case .unsupported: return "unsupported"
    case .unauthorized: return "unauthorized"
    case .poweredOff: return "poweredOff"
    case .poweredOn: return "poweredOn"
    @unknown default: return "future(\(state.rawValue))"
    }
}

private func deviceID(from name: String?) -> String? {
    guard let name else { return nil }
    let normalized = name.uppercased()
    guard let prefixRange = normalized.range(of: beaconPrefix) else { return nil }
    let suffix = String(normalized[prefixRange.upperBound...].prefix(6))
    guard suffix.count == 6,
          suffix.unicodeScalars.allSatisfy({ CharacterSet(charactersIn: "0123456789ABCDEF").contains($0) })
    else { return nil }
    return suffix
}

private func jsonString(_ object: [String: Any]) -> String {
    do {
        let data = try JSONSerialization.data(withJSONObject: object, options: [])
        return String(decoding: data, as: UTF8.self)
    } catch {
        return "{\"error\":\"原生蓝牙结果序列化失败\"}"
    }
}

private struct Target {
    let address: String?
    let deviceID: String?
}

private enum NativeResult<Value> {
    case success(Value)
    case failure(String)
}

private final class ResponseBox: @unchecked Sendable {
    private let lock = NSLock()
    private var completed = false
    private(set) var value = ""
    let semaphore = DispatchSemaphore(value: 0)

    func complete(_ value: String) {
        lock.lock()
        defer { lock.unlock() }
        guard !completed else { return }
        completed = true
        self.value = value
        semaphore.signal()
    }
}

private final class NativeBluetooth: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    private var central: CBCentralManager!
    private var readyCallbacks: [(NativeResult<Void>) -> Void] = []
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var names: [UUID: String] = [:]
    private var signalStrength: [UUID: Int] = [:]

    private var scanToken: UUID?
    private var scanCompletion: (([String: Any]) -> Void)?
    private var observed = Set<UUID>()
    private var candidates = Set<UUID>()
    private var unresolved = Set<UUID>()

    private var searchToken: UUID?
    private var searchTarget: Target?
    private var connectionCompletion: ((NativeResult<CBPeripheral>) -> Void)?
    private var activePeripheralID: UUID?
    private var connectedDeviceID: String?
    private var control: CBCharacteristic?
    private var ota: CBCharacteristic?

    private var writePackets: [Data] = []
    private var writeIndex = 0
    private var writeCharacteristic: CBCharacteristic?
    private var writeCompletion: ((NativeResult<Void>) -> Void)?
    private var disconnectCompletion: ((NativeResult<Void>) -> Void)?

    override init() {
        super.init()
        central = CBCentralManager(
            delegate: self,
            queue: nil,
            options: [CBCentralManagerOptionShowPowerAlertKey: true]
        )
    }

    func handle(_ request: [String: Any], completion: @escaping ([String: Any]) -> Void) {
        guard let command = request["command"] as? String else {
            completion(["error": "原生蓝牙命令无效"])
            return
        }
        switch command {
        case "scan": scan(seconds: request["seconds"] as? Double ?? 15, completion: completion)
        case "identify": identify(request, completion: completion)
        case "apply": apply(request, completion: completion)
        case "heartbeat": heartbeat(request, completion: completion)
        case "disconnect":
            disconnect { result in completion(self.response(result)) }
        case "status":
            let connected = currentPeripheral()?.state == .connected
            let reportedDeviceID: Any = connected ? (connectedDeviceID.map { $0 as Any } ?? NSNull()) : NSNull()
            completion(["connected": connected, "device_id": reportedDeviceID])
        case "ota": performOTA(request, completion: completion)
        default: completion(["error": "不支持的原生蓝牙命令"])
        }
    }

    private func identify(_ request: [String: Any], completion: @escaping ([String: Any]) -> Void) {
        let target = target(from: request)
        let wasConnected = currentPeripheral()?.state == .connected
        ensureConnected(target) { result in
            switch result {
            case .failure(let error): completion(["error": error])
            case .success:
                self.write([Data([0xC3, 1, 4, 0])], to: self.control) { writeResult in
                    if case .failure(let error) = writeResult { completion(["error": error]); return }
                    if wasConnected { completion(["ok": true]); return }
                    self.disconnect { result in completion(self.response(result)) }
                }
            }
        }
    }

    private func apply(_ request: [String: Any], completion: @escaping ([String: Any]) -> Void) {
        let target = target(from: request)
        guard let encoded = request["packets"] as? [String] else {
            completion(["error": "配置数据格式无效"]); return
        }
        let packets = encoded.compactMap { Data(base64Encoded: $0) }
        guard packets.count == encoded.count else {
            completion(["error": "配置数据格式无效"]); return
        }
        ensureConnected(target) { result in
            switch result {
            case .failure(let error): completion(["error": error])
            case .success:
                self.connectedDeviceID = target.deviceID ?? self.connectedDeviceID
                self.write(packets, to: self.control) { result in completion(self.response(result)) }
            }
        }
    }

    private func heartbeat(_ request: [String: Any], completion: ([String: Any]) -> Void) {
        guard let peripheral = currentPeripheral(), peripheral.state == .connected, let control else {
            completion(["error": "灯板未连接"]); return
        }
        let sequence = UInt8(request["sequence"] as? Int ?? 0)
        peripheral.writeValue(Data([0xC3, 1, 1, sequence]), for: control, type: .withoutResponse)
        completion(["ok": true])
    }

    private func performOTA(_ request: [String: Any], completion: @escaping ([String: Any]) -> Void) {
        guard let encoded = request["firmware"] as? String,
              let firmware = Data(base64Encoded: encoded), !firmware.isEmpty else {
            completion(["error": "固件文件格式无效"]); return
        }
        let target = target(from: request)
        ensureConnected(target) { result in
            switch result {
            case .failure(let error): completion(["error": error])
            case .success:
                self.connectedDeviceID = target.deviceID ?? self.connectedDeviceID
                self.write(self.otaPackets(firmware), to: self.ota) { result in
                    completion(self.response(result))
                }
            }
        }
    }

    private func response(_ result: NativeResult<Void>) -> [String: Any] {
        switch result {
        case .success: return ["ok": true]
        case .failure(let error): return ["error": error]
        }
    }

    private func target(from request: [String: Any]) -> Target {
        Target(address: request["address"] as? String, deviceID: request["device_id"] as? String)
    }

    private func whenReady(_ completion: @escaping (NativeResult<Void>) -> Void) {
        switch central.state {
        case .poweredOn: completion(.success(()))
        case .unauthorized: completion(.failure("Beacon 没有 macOS 蓝牙权限"))
        case .unsupported: completion(.failure("这台 Mac 不支持低功耗蓝牙"))
        case .poweredOff: completion(.failure("Mac 蓝牙已关闭"))
        default: readyCallbacks.append(completion)
        }
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        let callbacks = readyCallbacks
        readyCallbacks.removeAll()
        guard !callbacks.isEmpty else { return }
        let result: NativeResult<Void>
        switch central.state {
        case .poweredOn: result = .success(())
        case .unauthorized: result = .failure("Beacon 没有 macOS 蓝牙权限")
        case .unsupported: result = .failure("这台 Mac 不支持低功耗蓝牙")
        case .poweredOff: result = .failure("Mac 蓝牙已关闭")
        default: readyCallbacks.append(contentsOf: callbacks); return
        }
        callbacks.forEach { $0(result) }
    }

    private func scan(seconds: Double, completion: @escaping ([String: Any]) -> Void) {
        whenReady { result in
            switch result {
            case .failure(let error): completion(["error": error])
            case .success:
                self.observed.removeAll(); self.candidates.removeAll(); self.unresolved.removeAll()
                self.scanCompletion = completion
                let token = UUID(); self.scanToken = token
                self.central.scanForPeripherals(
                    withServices: nil,
                    options: [CBCentralManagerScanOptionAllowDuplicatesKey: true]
                )
                DispatchQueue.main.asyncAfter(deadline: .now() + min(max(seconds, 3), 30)) {
                    guard self.scanToken == token else { return }
                    self.finishScan()
                }
            }
        }
    }

    private func finishScan() {
        central.stopScan(); scanToken = nil
        let completion = scanCompletion; scanCompletion = nil
        var devices = candidates.compactMap { id -> [String: Any]? in
            guard let name = names[id], let deviceID = deviceID(from: name) else { return nil }
            return [
                "name": name, "device_id": deviceID, "address": id.uuidString,
                "rssi": signalStrength[id] ?? 0,
                "connected": peripherals[id]?.state == .connected,
            ]
        }.sorted { ($0["rssi"] as? Int ?? Int.min) > ($1["rssi"] as? Int ?? Int.min) }
        // Connected boards often stop advertising, so they are absent from candidates.
        // Keep the active board visible in Device Manager after a rescan.
        if let peripheral = currentPeripheral(), peripheral.state == .connected,
           let deviceID = connectedDeviceID ?? deviceID(from: names[peripheral.identifier]),
           !devices.contains(where: { ($0["device_id"] as? String)?.caseInsensitiveCompare(deviceID) == .orderedSame }) {
            devices.insert([
                "name": names[peripheral.identifier] ?? "\(beaconPrefix)\(deviceID)",
                "device_id": deviceID,
                "address": peripheral.identifier.uuidString,
                "rssi": signalStrength[peripheral.identifier] ?? NSNull(),
                "connected": true,
            ], at: 0)
        }
        completion?([
            "backend": "CoreBluetooth", "state": stateName(central.state), "devices": devices,
            "observed_count": observed.count, "candidate_count": candidates.count,
            "unresolved_count": unresolved.count,
        ])
    }

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        let id = peripheral.identifier
        peripherals[id] = peripheral; peripheral.delegate = self
        observed.insert(id); signalStrength[id] = RSSI.intValue
        let name = (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? peripheral.name
        if let name { names[id] = name }
        let services = Set((advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? [])
            .map { $0.uuidString.uppercased() })
        let matchedID = deviceID(from: name)
        let serviceMatches = services.contains(beaconService.uuidString.uppercased())
        if serviceMatches || matchedID != nil { candidates.insert(id) }
        if serviceMatches && matchedID == nil { unresolved.insert(id) } else { unresolved.remove(id) }

        if let target = searchTarget, matches(peripheral, target: target) {
            searchTarget = nil; searchToken = nil; central.stopScan(); connect(peripheral)
        }
    }

    private func matches(_ peripheral: CBPeripheral, target: Target) -> Bool {
        if let address = target.address,
           peripheral.identifier.uuidString.caseInsensitiveCompare(address) == .orderedSame { return true }
        if let expected = target.deviceID,
           let actual = deviceID(from: names[peripheral.identifier]),
           actual.caseInsensitiveCompare(expected) == .orderedSame { return true }
        return false
    }

    private func ensureConnected(
        _ target: Target,
        completion: @escaping (NativeResult<CBPeripheral>) -> Void
    ) {
        if let current = currentPeripheral(), current.state == .connected,
           matches(current, target: target), control != nil, ota != nil {
            completion(.success(current)); return
        }
        connectionCompletion = completion; control = nil; ota = nil
        if let peripheral = peripherals.values.first(where: { matches($0, target: target) }) {
            connect(peripheral); return
        }
        whenReady { result in
            switch result {
            case .failure(let error): self.finishConnection(.failure(error))
            case .success:
                self.searchTarget = target
                let token = UUID(); self.searchToken = token
                self.central.scanForPeripherals(
                    withServices: nil,
                    options: [CBCentralManagerScanOptionAllowDuplicatesKey: true]
                )
                DispatchQueue.main.asyncAfter(deadline: .now() + 15) {
                    guard self.searchToken == token else { return }
                    self.searchToken = nil; self.searchTarget = nil; self.central.stopScan()
                    self.finishConnection(.failure("没有发现目标灯板"))
                }
            }
        }
    }

    private func connect(_ peripheral: CBPeripheral) {
        activePeripheralID = peripheral.identifier
        peripheral.delegate = self
        if peripheral.state == .connected { peripheral.discoverServices([beaconService]) }
        else { central.connect(peripheral, options: nil) }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.delegate = self; peripheral.discoverServices([beaconService])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        finishConnection(.failure("连接灯板失败：\(error?.localizedDescription ?? "未知错误")"))
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        if activePeripheralID == peripheral.identifier {
            activePeripheralID = nil; connectedDeviceID = nil; control = nil; ota = nil
        }
        if let completion = disconnectCompletion {
            disconnectCompletion = nil
            if let error { completion(.failure("断开灯板失败：\(error.localizedDescription)")) }
            else { completion(.success(())) }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error { finishConnection(.failure("读取灯板服务失败：\(error.localizedDescription)")); return }
        guard let service = peripheral.services?.first(where: { $0.uuid == beaconService }) else {
            finishConnection(.failure("灯板固件缺少所需蓝牙服务")); return
        }
        peripheral.discoverCharacteristics([controlCharacteristic, otaCharacteristic], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error { finishConnection(.failure("读取灯板特征失败：\(error.localizedDescription)")); return }
        for characteristic in service.characteristics ?? [] {
            if characteristic.uuid == controlCharacteristic { control = characteristic }
            if characteristic.uuid == otaCharacteristic { ota = characteristic }
        }
        guard control != nil, ota != nil else {
            finishConnection(.failure("灯板固件缺少控制或升级特征")); return
        }
        connectedDeviceID = deviceID(from: names[peripheral.identifier]) ?? connectedDeviceID
        finishConnection(.success(peripheral))
    }

    private func finishConnection(_ result: NativeResult<CBPeripheral>) {
        let completion = connectionCompletion; connectionCompletion = nil; completion?(result)
    }

    private func write(
        _ packets: [Data],
        to characteristic: CBCharacteristic?,
        completion: @escaping (NativeResult<Void>) -> Void
    ) {
        guard let peripheral = currentPeripheral(), peripheral.state == .connected,
              let characteristic else { completion(.failure("灯板未连接或缺少写入特征")); return }
        guard !packets.isEmpty else { completion(.success(())); return }
        writePackets = packets; writeIndex = 0; writeCharacteristic = characteristic
        writeCompletion = completion
        peripheral.writeValue(packets[0], for: characteristic, type: .withResponse)
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard writeCompletion != nil else { return }
        if let error { finishWrite(.failure("蓝牙写入失败：\(error.localizedDescription)")); return }
        writeIndex += 1
        if writeIndex >= writePackets.count { finishWrite(.success(())) }
        else if let writeCharacteristic {
            peripheral.writeValue(writePackets[writeIndex], for: writeCharacteristic, type: .withResponse)
        }
    }

    private func finishWrite(_ result: NativeResult<Void>) {
        let completion = writeCompletion
        writeCompletion = nil; writePackets.removeAll(); writeCharacteristic = nil
        completion?(result)
    }

    private func disconnect(completion: @escaping (NativeResult<Void>) -> Void) {
        guard let peripheral = currentPeripheral(), peripheral.state != .disconnected else {
            connectedDeviceID = nil; control = nil; ota = nil; completion(.success(())); return
        }
        disconnectCompletion = completion; central.cancelPeripheralConnection(peripheral)
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) {
            guard let pending = self.disconnectCompletion else { return }
            self.disconnectCompletion = nil; pending(.failure("断开灯板超时"))
        }
    }

    private func currentPeripheral() -> CBPeripheral? {
        guard let activePeripheralID else { return nil }
        return peripherals[activePeripheralID]
    }

    private func otaPackets(_ firmware: Data) -> [Data] {
        let size = UInt32(firmware.count)
        var packets = [Data([1, UInt8(size & 0xff), UInt8((size >> 8) & 0xff),
                             UInt8((size >> 16) & 0xff), UInt8((size >> 24) & 0xff)])]
        var offset = 0
        while offset < firmware.count {
            let end = min(offset + 240, firmware.count)
            var packet = Data([2]); packet.append(contentsOf: firmware[offset..<end]); packets.append(packet)
            offset = end
        }
        packets.append(Data([3])); return packets
    }
}

private var nativeBluetooth: NativeBluetooth?

@_cdecl("beacon_macos_request_json")
public func beaconMacOSRequestJSON(_ requestPointer: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>? {
    guard let requestPointer else { return strdup("{\"error\":\"原生蓝牙请求为空\"}") }
    let requestText = String(cString: requestPointer)
    guard let data = requestText.data(using: .utf8),
          let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return strdup("{\"error\":\"原生蓝牙请求格式无效\"}")
    }
    let box = ResponseBox()
    DispatchQueue.main.async {
        if nativeBluetooth == nil { nativeBluetooth = NativeBluetooth() }
        nativeBluetooth?.handle(request) { response in box.complete(jsonString(response)) }
    }
    let command = request["command"] as? String
    let timeout: TimeInterval = command == "ota" ? 600 : (command == "scan" ? 45 : 40)
    if box.semaphore.wait(timeout: .now() + timeout) == .timedOut {
        box.complete("{\"error\":\"原生蓝牙操作超时\"}")
    }
    return strdup(box.value)
}

@_cdecl("beacon_macos_free_string")
public func beaconMacOSFreeString(_ pointer: UnsafeMutablePointer<CChar>?) {
    free(pointer)
}
