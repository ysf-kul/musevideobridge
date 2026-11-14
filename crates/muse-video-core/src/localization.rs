//! Repository-owned catalogs. Turkish is the default independently of OS locale.
use std::sync::OnceLock;
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub enum Locale {
    #[default]
    Turkish,
    English,
}
static TR: OnceLock<serde_json::Value> = OnceLock::new();
static EN: OnceLock<serde_json::Value> = OnceLock::new();
pub fn text(locale: Locale, key: &str) -> &'static str {
    let (cache, source) = match locale {
        Locale::Turkish => (&TR, include_str!("../../../localization/tr.json")),
        Locale::English => (&EN, include_str!("../../../localization/en.json")),
    };
    let catalog = cache.get_or_init(|| {
        serde_json::from_str(source).expect("validated embedded localization catalog")
    });
    catalog[key].as_str().unwrap_or_else(|| {
        catalog["error.generic"]
            .as_str()
            .expect("generic error translation")
    })
}
pub fn error_message(locale: Locale, code: &str) -> &'static str {
    text(locale, &format!("error.{code}"))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn catalogs_have_matching_keys_and_turkish_default() {
        let tr: serde_json::Value =
            serde_json::from_str(include_str!("../../../localization/tr.json")).unwrap();
        let en: serde_json::Value =
            serde_json::from_str(include_str!("../../../localization/en.json")).unwrap();
        assert_eq!(
            tr.as_object().unwrap().keys().collect::<Vec<_>>(),
            en.as_object().unwrap().keys().collect::<Vec<_>>()
        );
        assert_eq!(Locale::default(), Locale::Turkish);
        assert_eq!(
            error_message(Locale::English, "unknown"),
            text(Locale::English, "error.generic")
        );
        for v in tr
            .as_object()
            .unwrap()
            .values()
            .chain(en.as_object().unwrap().values())
        {
            assert!(!v.as_str().unwrap().is_empty());
        }
    }
}
