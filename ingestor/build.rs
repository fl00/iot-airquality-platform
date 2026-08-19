//! Build script for compiling air_quality.proto with prost-build

fn main() {
    let proto_file = "../iot-airquality-contracts/proto/air_quality.proto";
    if std::path::Path::new(proto_file).exists() && std::env::var("PROTOC").is_ok() {
        println!("cargo:rerun-if-changed={}", proto_file);
        #[cfg(feature = "prost-build")]
        {
            let mut config = prost_build::Config::new();
            config.compile_protos(&[proto_file], &["../iot-airquality-contracts/proto"]).unwrap();
        }
    }
}
