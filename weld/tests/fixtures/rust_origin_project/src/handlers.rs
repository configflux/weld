use core::fmt::Debug;
use tokio::runtime::Runtime;
use myapi::build_map;

pub trait Handler: Debug {
    fn handle(&self) -> Runtime;
}

pub fn use_local() {
    let _ = build_map();
}
