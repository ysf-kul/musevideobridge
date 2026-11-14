pub mod container;
pub mod jpeg;
pub mod localization;
pub mod media;
pub mod protocol;
pub mod serial;
pub mod storage;

use serde::Serialize;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};

#[derive(Debug, Clone, Serialize)]
pub struct Error {
    pub code: String,
    pub details: String,
}
impl Error {
    pub fn new(code: &str, details: impl ToString) -> Self {
        Self {
            code: code.into(),
            details: details.to_string(),
        }
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.details)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self::new(
            if e.kind() == std::io::ErrorKind::PermissionDenied {
                "permission_denied"
            } else {
                "io_error"
            },
            e,
        )
    }
}
impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Self::new("invalid_json", e)
    }
}
pub type Result<T> = std::result::Result<T, Error>;

#[derive(Clone, Default)]
pub struct Cancellation(Arc<AtomicBool>);
impl Cancellation {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::SeqCst);
    }
    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
    pub fn check(&self) -> Result<()> {
        if self.is_cancelled() {
            Err(Error::new("cancelled", "Operation cancelled"))
        } else {
            Ok(())
        }
    }
}
