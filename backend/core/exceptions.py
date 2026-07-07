from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.schemas.response import error_response


class AppException(Exception):
    def __init__(self, message: str = "business error", status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def app_exception_handler(
    request: Request, exc: AppException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(message=exc.message),
    )


async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "http error"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(message=message),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_response(message="validation error"),
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_response(message="internal server error"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
