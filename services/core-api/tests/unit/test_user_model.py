from src.models.user import AppUser


def test_app_user_has_default_role_and_status_columns() -> None:
    columns = AppUser.__table__.c

    assert columns.role.default.arg == "USER"
    assert str(columns.role.server_default.arg) == "'USER'"
    assert columns.status.default.arg == "ACTIVE"
    assert str(columns.status.server_default.arg) == "'ACTIVE'"
