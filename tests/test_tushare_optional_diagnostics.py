from datasource.tushare_provider import _error_type, _status_from_exception


def test_optional_endpoint_error_categories_are_specific():
    assert _error_type(Exception("没有访问该接口的权限")) == "NOT_AUTHORIZED"
    assert _status_from_exception(Exception("没有访问该接口的权限")) == "permission_denied"
    assert _error_type(Exception("参数错误")) == "INVALID_PARAMETER"
    assert _error_type(TimeoutError("timeout")) == "TIMEOUT"
    assert _error_type(Exception("unexpected")) == "PROVIDER_ERROR"
