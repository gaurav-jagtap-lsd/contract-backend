from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        response.data = {
            "success": False,
            "error": {
                "code": response.status_code,
                "message": _flatten_errors(response.data),
                "detail": response.data,
            },
        }
    return response


def _flatten_errors(data):
    if isinstance(data, dict):
        messages = []
        for key, value in data.items():
            if key in ("detail", "non_field_errors"):
                messages.append(str(value) if not isinstance(value, list) else " ".join(str(v) for v in value))
            else:
                messages.append(f"{key}: {value}")
        return " | ".join(messages) if messages else "An error occurred."
    if isinstance(data, list):
        return " ".join(str(item) for item in data)
    return str(data)


class ContractVaultException(Exception):
    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code
        super().__init__(message)


def success_response(data=None, message="Success", status_code=200):
    return Response(
        {"success": True, "message": message, "data": data},
        status=status_code,
    )


def error_response(message="An error occurred.", status_code=400, detail=None):
    payload = {"success": False, "error": {"message": message, "code": status_code}}
    if detail:
        payload["error"]["detail"] = detail
    return Response(payload, status=status_code)
