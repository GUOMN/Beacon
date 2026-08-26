use serde::Serialize;
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};
use windows::{
    core::{GUID, Ref},
    Devices::Bluetooth::{
        Advertisement::{
            BluetoothLEAdvertisementReceivedEventArgs, BluetoothLEAdvertisementWatcher,
            BluetoothLEScanningMode,
        },
        BluetoothCacheMode, BluetoothConnectionStatus, BluetoothLEDevice,
        GenericAttributeProfile::{
            GattCharacteristic, GattCommunicationStatus, GattWriteOption,
        },
    },
    Foundation::TypedEventHandler,
    Storage::Streams::{DataReader, DataWriter},
};

const DEVICE_PREFIX: &str = "Codex-Light-";
const SERVICE_GUID: GUID = GUID::from_u128(0x0100c310_7625_819e_934c_32b8e4177d6a);
const CONTROL_GUID: GUID = GUID::from_u128(0x0200c310_7625_819e_934c_32b8e4177d6a);
const OTA_GUID: GUID = GUID::from_u128(0x0300c310_7625_819e_934c_32b8e4177d6a);
const INFO_GUID: GUID = GUID::from_u128(0x0400c310_7625_819e_934c_32b8e4177d6a);

#[derive(Clone, Debug, Serialize)]
pub struct ScannedBoard {
    pub name: String,
    pub device_id: String,
    pub address: String,
    pub rssi: i16,
    pub connected: bool,
}

#[derive(Clone)]
pub struct Connection {
    device: BluetoothLEDevice,
    pub device_id: String,
    pub address: String,
    control: GattCharacteristic,
    ota: GattCharacteristic,
    info: GattCharacteristic,
    pub firmware_version: Option<String>,
    pub partition: Option<String>,
    pub build_date: Option<String>,
    pub build_time: Option<String>,
}

impl Connection {
    pub fn is_connected(&self) -> bool {
        self.device.ConnectionStatus().ok() == Some(BluetoothConnectionStatus::Connected)
    }

    pub fn close(&self) -> Result<(), String> {
        self.device.Close().map_err(|error| error.to_string())
    }

    async fn write(
        characteristic: &GattCharacteristic,
        bytes: &[u8],
        with_response: bool,
    ) -> Result<(), String> {
        let option = if with_response {
            GattWriteOption::WriteWithResponse
        } else {
            GattWriteOption::WriteWithoutResponse
        };
        let operation = {
            let writer = DataWriter::new().map_err(|error| error.to_string())?;
            writer.WriteBytes(bytes).map_err(|error| error.to_string())?;
            let buffer = writer.DetachBuffer().map_err(|error| error.to_string())?;
            characteristic
                .WriteValueWithOptionAsync(&buffer, option)
                .map_err(|error| error.to_string())?
        };
        let status = operation
            .await
            .map_err(|error| error.to_string())?;
        (status == GattCommunicationStatus::Success)
            .then_some(())
            .ok_or_else(|| format!("Windows 原生 GATT 写入失败：{status:?}"))
    }

    pub async fn write_control(&self, bytes: &[u8], with_response: bool) -> Result<(), String> {
        Self::write(&self.control, bytes, with_response).await
    }

    pub async fn write_ota(&self, bytes: &[u8]) -> Result<(), String> {
        Self::write(&self.ota, bytes, true).await
    }

    pub async fn read_info(&self) -> Result<String, String> {
        let result = self
            .info
            .ReadValueWithCacheModeAsync(BluetoothCacheMode::Uncached)
            .map_err(|error| error.to_string())?
            .await
            .map_err(|error| error.to_string())?;
        if result.Status().map_err(|error| error.to_string())? != GattCommunicationStatus::Success {
            return Err("读取灯板固件信息失败".into());
        }
        let buffer = result.Value().map_err(|error| error.to_string())?;
        let reader = DataReader::FromBuffer(&buffer).map_err(|error| error.to_string())?;
        let mut bytes = vec![0; reader.UnconsumedBufferLength().map_err(|error| error.to_string())? as usize];
        reader.ReadBytes(&mut bytes).map_err(|error| error.to_string())?;
        String::from_utf8(bytes).map_err(|_| "灯板固件信息不是有效文本".to_string())
    }
}

fn device_id_from_name(name: &str) -> Option<String> {
    let start = name.find(DEVICE_PREFIX)? + DEVICE_PREFIX.len();
    let id: String = name[start..].chars().take(6).collect();
    (id.len() == 6 && id.chars().all(|value| value.is_ascii_hexdigit()))
        .then(|| id.to_ascii_uppercase())
}

fn address_string(address: u64) -> String {
    let bytes = address.to_be_bytes();
    bytes[2..]
        .iter()
        .map(|value| format!("{value:02X}"))
        .collect::<Vec<_>>()
        .join(":")
}

fn scan_blocking(seconds: u64) -> Result<Vec<ScannedBoard>, String> {
    let watcher = BluetoothLEAdvertisementWatcher::new()
        .map_err(|error| format!("初始化 Windows 原生蓝牙扫描失败：{error}"))?;
    watcher
        .SetScanningMode(BluetoothLEScanningMode::Active)
        .map_err(|error| format!("设置 Windows 原生蓝牙扫描模式失败：{error}"))?;
    let _ = watcher.SetAllowExtendedAdvertisements(true);

    let boards = Arc::new(Mutex::new(HashMap::<u64, ScannedBoard>::new()));
    let received_boards = boards.clone();
    let handler = TypedEventHandler::<
        BluetoothLEAdvertisementWatcher,
        BluetoothLEAdvertisementReceivedEventArgs,
    >::new(move |_sender, args: Ref<BluetoothLEAdvertisementReceivedEventArgs>| {
        let Some(args) = args.as_ref() else { return Ok(()) };
        let advertisement = args.Advertisement()?;
        let name = advertisement.LocalName()?.to_string_lossy();
        let service_matches = advertisement
            .ServiceUuids()?
            .into_iter()
            .any(|uuid| uuid == SERVICE_GUID);
        let Some(device_id) = device_id_from_name(&name) else {
            if service_matches {
                // The active watcher will also receive the scan response carrying
                // the local name. Do not connect merely to resolve a missing name.
            }
            return Ok(());
        };
        let address = args.BluetoothAddress()?;
        let board = ScannedBoard {
            name,
            device_id,
            address: address_string(address),
            rssi: args.RawSignalStrengthInDBm()?,
            connected: false,
        };
        if let Ok(mut held) = received_boards.lock() {
            held.insert(address, board);
        }
        Ok(())
    });
    let token = watcher
        .Received(&handler)
        .map_err(|error| format!("监听 Windows 原生蓝牙广播失败：{error}"))?;
    watcher
        .Start()
        .map_err(|error| format!("启动 Windows 原生蓝牙扫描失败：{error}"))?;
    std::thread::sleep(Duration::from_secs(seconds));
    watcher.Stop().map_err(|error| format!("停止 Windows 原生蓝牙扫描失败：{error}"))?;
    let _ = watcher.RemoveReceived(token);

    let mut result = boards
        .lock()
        .map_err(|_| "Windows 原生蓝牙扫描结果锁异常".to_string())?
        .values()
        .cloned()
        .collect::<Vec<_>>();
    result.sort_by(|left, right| right.rssi.cmp(&left.rssi));
    Ok(result)
}

pub async fn scan(seconds: u64) -> Result<Vec<ScannedBoard>, String> {
    tokio::task::spawn_blocking(move || scan_blocking(seconds))
        .await
        .map_err(|error| format!("Windows 原生蓝牙扫描任务异常：{error}"))?
}

fn parse_address(address: &str) -> Result<u64, String> {
    let compact = address.replace([':', '-'], "");
    u64::from_str_radix(&compact, 16).map_err(|_| "灯板蓝牙地址无效".to_string())
}

async fn characteristic(
    service: &windows::Devices::Bluetooth::GenericAttributeProfile::GattDeviceService,
    uuid: GUID,
) -> Result<GattCharacteristic, String> {
    let uncached = match service
        .GetCharacteristicsForUuidWithCacheModeAsync(uuid, BluetoothCacheMode::Uncached)
    {
        Ok(operation) => operation.await.ok(),
        Err(_) => None,
    };
    let result = match uncached {
        Some(result) if result.Status().ok() == Some(GattCommunicationStatus::Success) => result,
        _ => service
            .GetCharacteristicsForUuidWithCacheModeAsync(uuid, BluetoothCacheMode::Cached)
            .map_err(|error| error.to_string())?
            .await
            .map_err(|error| error.to_string())?,
    };
    if result.Status().map_err(|error| error.to_string())? != GattCommunicationStatus::Success {
        return Err("读取灯板 GATT 特征失败".into());
    }
    result
        .Characteristics()
        .map_err(|error| error.to_string())?
        .GetAt(0)
        .map_err(|_| "灯板固件缺少所需蓝牙特征".to_string())
}

pub async fn connect(address: &str, device_id: &str) -> Result<Connection, String> {
    let numeric_address = parse_address(address)?;
    let device = BluetoothLEDevice::FromBluetoothAddressAsync(numeric_address)
        .map_err(|error| format!("创建 Windows 原生蓝牙设备失败：{error}"))?
        .await
        .map_err(|error| format!("连接灯板失败：{error}"))?;
    let uncached = match device
        .GetGattServicesForUuidWithCacheModeAsync(SERVICE_GUID, BluetoothCacheMode::Uncached)
    {
        Ok(operation) => operation.await.ok(),
        Err(_) => None,
    };
    let services_result = match uncached {
        Some(result) if result.Status().ok() == Some(GattCommunicationStatus::Success) => result,
        _ => device
            .GetGattServicesForUuidWithCacheModeAsync(SERVICE_GUID, BluetoothCacheMode::Cached)
            .map_err(|error| error.to_string())?
            .await
            .map_err(|error| error.to_string())?,
    };
    if services_result.Status().map_err(|error| error.to_string())?
        != GattCommunicationStatus::Success
    {
        return Err("连接灯板服务失败".into());
    }
    let service = services_result
        .Services()
        .map_err(|error| error.to_string())?
        .GetAt(0)
        .map_err(|_| "目标设备不是受支持的灯板".to_string())?;
    let control = characteristic(&service, CONTROL_GUID).await?;
    let ota = characteristic(&service, OTA_GUID).await?;
    let info = characteristic(&service, INFO_GUID).await?;
    let mut connection = Connection {
        device,
        device_id: device_id.to_ascii_uppercase(),
        address: address.to_string(),
        control,
        ota,
        info,
        firmware_version: None,
        partition: None,
        build_date: None,
        build_time: None,
    };
    if let Ok(value) = connection.read_info().await {
        let (version, partition, date, time) = parse_firmware_info(&value);
        connection.firmware_version = version;
        connection.partition = partition;
        connection.build_date = date;
        connection.build_time = time;
    }
    Ok(connection)
}

pub fn parse_firmware_info(
    value: &str,
) -> (Option<String>, Option<String>, Option<String>, Option<String>) {
    let mut fields = value.trim().split('|');
    let version = fields.next().filter(|item| !item.is_empty()).map(str::to_string);
    let partition = fields.next().filter(|item| !item.is_empty()).map(str::to_string);
    let build = fields.next().unwrap_or_default().trim();
    let (date, time) = build
        .rsplit_once(' ')
        .map(|(date, time)| (Some(date.to_string()), Some(time.to_string())))
        .unwrap_or_else(|| (None, (!build.is_empty()).then(|| build.to_string())));
    (version, partition, date, time)
}
