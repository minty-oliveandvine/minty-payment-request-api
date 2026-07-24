import logging

logger = logging.getLogger("minty-api")


class BillValidationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


def register_exception_handlers(api):
    @api.exception_handler(BillValidationError)
    def on_validation(request, exc):
        logger.warning("Validation error: %s", str(exc))
        return api.create_response(request, {"detail": str(exc)}, status=422)

    @api.exception_handler(PermissionDeniedError)
    def on_permission_denied(request, exc):
        logger.warning("Permission denied: %s", str(exc))
        return api.create_response(request, {"detail": str(exc)}, status=403)

    @api.exception_handler(Exception)
    def on_unhandled(request, exc):
        logger.exception("Unhandled error: %s", str(exc))
        return api.create_response(
            request, {"detail": "Internal server error"}, status=500
        )
