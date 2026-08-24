from asc.models import AppSetting, KeyVaultRef, compose_kv_ref, is_kv_ref, parse_kv_ref

URI_REF = (
    "@Microsoft.KeyVault(SecretUri="
    "https://kv-prod.vault.azure.net/secrets/DbPassword)"
)
URI_REF_VERSIONED = (
    "@Microsoft.KeyVault(SecretUri="
    "https://kv-prod.vault.azure.net/secrets/DbPassword/abc123)"
)
NAME_REF = "@Microsoft.KeyVault(VaultName=kv-prod;SecretName=DbPassword)"
NAME_REF_VERSIONED = (
    "@Microsoft.KeyVault(VaultName=kv-prod;SecretName=DbPassword;"
    "SecretVersion=abc123)"
)


def test_parse_secret_uri_form():
    ref = parse_kv_ref(URI_REF)
    assert ref == KeyVaultRef(vault="kv-prod", secret="DbPassword", raw=URI_REF)


def test_parse_secret_uri_with_version():
    ref = parse_kv_ref(URI_REF_VERSIONED)
    assert ref is not None
    assert ref.vault == "kv-prod" and ref.secret == "DbPassword"


def test_parse_vaultname_form():
    ref = parse_kv_ref(NAME_REF)
    assert ref is not None
    assert ref.vault == "kv-prod" and ref.secret == "DbPassword"


def test_parse_vaultname_with_version():
    ref = parse_kv_ref(NAME_REF_VERSIONED)
    assert ref is not None
    assert ref.secret == "DbPassword"


def test_parse_plain_value_returns_none():
    assert parse_kv_ref("hunter2") is None


def test_malformed_ref_is_kv_but_unparseable():
    v = "@Microsoft.KeyVault(garbage)"
    assert is_kv_ref(v) and parse_kv_ref(v) is None


def test_is_kv_ref_case_insensitive():
    assert is_kv_ref("@microsoft.keyvault(SecretUri=x)")


def test_compose_round_trips():
    raw = compose_kv_ref("kv-prod", "DbPassword")
    assert parse_kv_ref(raw) == KeyVaultRef("kv-prod", "DbPassword", raw)


def test_from_raw_coerces_null_value_to_empty_string():
    """Real App Service settings can come back with "value": null."""
    assert AppSetting.from_raw({"name": "K", "value": None}) == AppSetting("K", "", False)


def test_from_raw_tolerates_missing_value_and_slot_setting():
    assert AppSetting.from_raw({"name": "K"}) == AppSetting("K", "", False)


def test_kv_helpers_are_defensive_about_non_strings():
    assert parse_kv_ref(None) is None  # ty: ignore[invalid-argument-type]
    assert is_kv_ref(None) is False  # ty: ignore[invalid-argument-type]


def test_setting_kv_ref_property_and_matches():
    s = AppSetting(key="DB_PASSWORD", value=URI_REF, slot_setting=True)
    assert s.kv_ref is not None
    assert s.kv_ref.vault == "kv-prod"
    assert s.matches("db_pass") and not s.matches("nope")
