//! Placeholders for application names rust-idioms snippets reference but don't define,
//! plus imports the docs show once per page. Never std API shims.
pub use std::fs;
pub use std::sync::{Arc, Mutex};
pub use std::time::Duration;

#[derive(Debug, Clone, Default)]
pub struct Config;
impl Config {
    pub fn load() -> Config {
        Config
    }
}
#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct User {
    pub id: u64,
    pub name: String,
}
#[derive(Debug, Clone, Default)]
pub struct State;
#[derive(Debug, Clone, Default)]
pub struct Socket;
#[derive(Debug)]
pub struct Request;
impl Request {
    pub fn param<T: std::str::FromStr>(&mut self, _k: &str) -> Option<T> {
        None
    }
}
#[derive(Debug, thiserror::Error)]
pub enum MyError {
    #[error("bad id")]
    BadId,
}
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("not found: {0}")]
    NotFound(String),
}

#[derive(Debug, PartialEq)]
pub enum ConfigError {
    Empty,
    Bad(String),
}
pub fn parse_config(s: &str) -> Result<u32, ConfigError> {
    (!s.is_empty()).ok_or(ConfigError::Empty)?;
    s.parse().map_err(|_| ConfigError::Bad(s.into()))
}

pub fn load_config() -> Config {
    Config
}
pub async fn do_io() {}
pub async fn compute() -> u64 {
    0
}
pub async fn fetch(_url: &str) -> Result<String, anyhow::Error> {
    Ok(String::new())
}
pub fn heavy_sync<T>(_input: T) -> u64 {
    0
}
pub fn slugify(s: &str) -> String {
    s.to_lowercase()
}
pub fn fill_from_somewhere(_buf: &mut [std::mem::MaybeUninit<u8>]) -> usize {
    0
}
pub async fn use_it<T>(_t: T) {}
pub fn load_user(_path: &str) -> Result<User, StoreError> {
    Ok(User::default())
}
pub fn write_all(_b: &[u8]) -> std::io::Result<()> {
    Ok(())
}
