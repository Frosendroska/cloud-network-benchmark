from cloud_network_benchmark.errors import CollisionError, PersistenceError, UsageError, ValidationError


def test_error_exit_codes_and_shape() -> None:
    cases = [(UsageError("u"), 2), (ValidationError("v", "config"), 3), (CollisionError("c"), 4), (PersistenceError("p"), 5)]
    for error, code in cases:
        assert error.exit_code == code
        assert error.as_dict()["error"]["message"] == str(error)
        assert "secret" not in str(error).lower()
