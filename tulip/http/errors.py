"""http related errors."""

__all__ = ['HttpException', 'HttpStatusException',
           'IncompleteRead', 'BadStatusLine', 'LineTooLong', 'InvalidHeader']

import http.client


class HttpException(http.client.HTTPException):

    code = None
    headers = ()


class HttpStatusException(HttpException):

    def __init__(self, code, headers=None, message=''):
        self.code = code
        self.headers = headers
        self.message = message


class BadRequestException(HttpException):

    code = 400


class IncompleteRead(BadRequestException, http.client.IncompleteRead):
    pass


class BadStatusLine(BadRequestException, http.client.BadStatusLine):
    pass


class LineTooLong(BadRequestException, http.client.LineTooLong):
    pass


class InvalidHeader(BadRequestException):

    def __init__(self, hdr):
        super().__init__('Invalid HTTP Header: {}'.format(hdr))
        self.hdr = hdr
