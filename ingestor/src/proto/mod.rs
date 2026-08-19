//! Generated Prost Protocol Buffers definition for `iot.airquality`

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, ::prost::Enumeration)]
#[repr(i32)]
pub enum SensorStatus {
    StatusOk = 0,
    StatusWarmingUp = 1,
    StatusDegraded = 2,
    StatusFault = 3,
}

#[derive(Clone, PartialEq, ::prost::Message)]
pub struct FullSample {
    #[prost(float, tag = "1")]
    pub temperature_celsius: f32,
    #[prost(float, tag = "2")]
    pub humidity_percent: f32,
    #[prost(uint32, tag = "3")]
    pub co2_ppm: u32,
    #[prost(float, tag = "4")]
    pub pm25_ug_m3: f32,
    #[prost(float, tag = "5")]
    pub pm10_ug_m3: f32,
    #[prost(float, tag = "6")]
    pub tvoc_ppb: f32,
    #[prost(float, tag = "7")]
    pub pressure_hpa: f32,
}

#[derive(Clone, PartialEq, ::prost::Message)]
pub struct DeltaSample {
    #[prost(uint32, tag = "1")]
    pub time_offset_sec: u32,
    #[prost(sint32, tag = "2")]
    pub co2_delta_ppm: i32,
    #[prost(sint32, tag = "3")]
    pub temp_delta_centi_deg: i32,
    #[prost(sint32, tag = "4")]
    pub hum_delta_centi_pct: i32,
    #[prost(sint32, tag = "5")]
    pub pm25_delta_centi_ug: i32,
}

#[derive(Clone, PartialEq, ::prost::Message)]
pub struct DeltaBatch {
    #[prost(message, repeated, tag = "1")]
    pub samples: ::prost::alloc::vec::Vec<DeltaSample>,
}

#[derive(Clone, PartialEq, ::prost::Message)]
pub struct AirQualityPacket {
    #[prost(string, tag = "1")]
    pub device_id: ::prost::alloc::string::String,
    #[prost(uint32, tag = "2")]
    pub sequence_number: u32,
    #[prost(uint64, tag = "3")]
    pub base_timestamp_sec: u64,
    #[prost(uint32, tag = "4")]
    pub battery_millivolts: u32,
    #[prost(sint32, tag = "5")]
    pub rssi_dbm: i32,
    #[prost(enumeration = "SensorStatus", tag = "6")]
    pub status: i32,
    #[prost(oneof = "air_quality_packet::Payload", tags = "7, 8")]
    pub payload: ::core::option::Option<air_quality_packet::Payload>,
}

pub mod air_quality_packet {
    #[derive(Clone, PartialEq, ::prost::Oneof)]
    pub enum Payload {
        #[prost(message, tag = "7")]
        FullSample(super::FullSample),
        #[prost(message, tag = "8")]
        DeltaBatch(super::DeltaBatch),
    }
}
