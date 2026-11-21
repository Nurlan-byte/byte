"""Authenticate POS credentials with an independently provisioned 256-bit master key."""

import argparse
import base64
import json
import os
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.errors import ServiceError

KEY_FILE_ENV = "BAKEPLAN_POS_MASTER_KEY_FILE"
FORMAT = b"BP1"
UNAVAILABLE = "Ключ хранилища недоступен. Проверьте настройку сервера."


def _windows_principals():
    import win32api
    import win32security as security

    token = security.OpenProcessToken(
        win32api.GetCurrentProcess(), security.TOKEN_QUERY
    )
    try:
        user = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    return (
        user,
        security.CreateWellKnownSid(security.WinLocalSystemSid),
        security.CreateWellKnownSid(security.WinBuiltinAdministratorsSid),
    )


def _create_file(path, material):
    if os.name != "nt":
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(material)
            stream.flush()
            os.fsync(stream.fileno())
        return
    import pywintypes
    import ntsecuritycon
    import win32con
    import win32file
    import win32security as security

    # Supply a protected DACL to CREATE_NEW itself; no inherited-access window.
    acl = security.ACL()
    for sid in _windows_principals():
        acl.AddAccessAllowedAce(
            security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid
        )
    descriptor = security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(
        security.SE_DACL_PROTECTED, security.SE_DACL_PROTECTED
    )
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = descriptor
    try:
        handle = win32file.CreateFile(
            str(path),
            win32con.GENERIC_WRITE,
            0,
            attrs,
            win32con.CREATE_NEW,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        try:
            win32file.WriteFile(handle, material)
            win32file.FlushFileBuffers(handle)
        finally:
            handle.Close()
    except pywintypes.error as exc:
        if exc.winerror in (80, 183):
            raise FileExistsError("Master key file already exists") from None
        raise OSError("Cannot create private master key file") from None


def init_key(path):
    """Create once. Never replace an existing file and never print the material."""
    _create_file(
        Path(path), base64.b64encode(AESGCM.generate_key(bit_length=256)) + b"\n"
    )


def _private_windows_handle(fd):
    import msvcrt
    import pywintypes
    import win32security as security

    try:
        allowed = _windows_principals()
        descriptor = security.GetSecurityInfo(
            msvcrt.get_osfhandle(fd),
            security.SE_FILE_OBJECT,
            security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION,
        )
        acl = descriptor.GetSecurityDescriptorDacl()
        if descriptor.GetSecurityDescriptorOwner() not in allowed or acl is None:
            return False
        for i in range(acl.GetAceCount()):
            ace = acl.GetAce(i)
            # Only ordinary deny ACEs and allow ACEs for our private principals.
            if ace[0][0] == security.ACCESS_DENIED_ACE_TYPE:
                continue
            if ace[0][0] != security.ACCESS_ALLOWED_ACE_TYPE or ace[2] not in allowed:
                return False
        return True
    except pywintypes.error:
        return False


def _load_key():
    try:
        path = Path(os.environ[KEY_FILE_ENV])
        if not path.is_absolute() or path.is_symlink() or path.is_junction():
            raise ValueError
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(os.open(path, flags), "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode):
                raise ValueError
            if os.name == "nt":
                if not _private_windows_handle(stream.fileno()):
                    raise ValueError
            elif meta.st_mode & 0o077 or meta.st_uid not in {0, os.geteuid()}:
                raise ValueError
            data = stream.read(128)
        key = base64.b64decode(data.rstrip(b"\r\n"), validate=True)
        if len(data) >= 128 or len(key) != 32:
            raise ValueError
        return key
    except KeyError, OSError, ValueError:
        raise ServiceError(503, UNAVAILABLE) from None


def available():
    try:
        _load_key()
        return True
    except ServiceError:
        return False


def _aad(connection_id, provider, bakery_id, location_id):
    return json.dumps(
        ["BakePlan POS credential", 1, connection_id, provider, bakery_id, location_id],
        separators=(",", ":"),
    ).encode()


def seal(api_key, connection_id, provider, bakery_id, location_id):
    nonce = os.urandom(12)
    ciphertext = AESGCM(_load_key()).encrypt(
        nonce,
        api_key.encode("ascii"),
        _aad(connection_id, provider, bakery_id, location_id),
    )
    return FORMAT + nonce + ciphertext


def unseal(blob, connection_id, provider, bakery_id, location_id):
    try:
        if not isinstance(blob, bytes) or not blob.startswith(FORMAT) or len(blob) < 32:
            raise ValueError
        return (
            AESGCM(_load_key())
            .decrypt(
                blob[3:15],
                blob[15:],
                _aad(connection_id, provider, bakery_id, location_id),
            )
            .decode("ascii")
        )
    except InvalidTag, ValueError:
        raise ServiceError(503, UNAVAILABLE) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "init", help="Create a new private master key file"
    ).add_argument("path")
    args = parser.parse_args()
    try:
        init_key(args.path)
    except OSError:
        parser.exit(
            1,
            "Could not create master key. Choose a new file in a private directory.\n",
        )
