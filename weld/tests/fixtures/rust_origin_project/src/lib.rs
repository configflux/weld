use std::collections::HashMap;
use serde::Serialize;
use crate::handlers::Handler;

pub mod handlers;

pub fn build_map() -> HashMap<String, String> {
    HashMap::new()
}
