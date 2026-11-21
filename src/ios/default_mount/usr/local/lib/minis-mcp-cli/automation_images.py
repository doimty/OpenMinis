"""Materialize only native automation screenshots for Minis' read_image tool.

The server chooses image bytes, never an output path. Capture creation requires
POSIX directory descriptors and O_NOFOLLOW; unsupported hosts fail closed.
"""
import base64
import binascii
import copy
import hashlib
import os
import stat
import struct
import uuid
import zlib


CAPTURE_DIR = ".minis-automation-captures"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 16 * 1024 * 1024
MAX_BASE64_BYTES = ((MAX_IMAGE_BYTES + 2) // 3) * 4


class AutomationImageError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AutomationImageError(message)


def dimensions(width, height):
    require(0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION
            and width * height <= MAX_PIXELS, "截图尺寸无效或超出限制。")
    return width, height


def png_dimensions(data):
    require(data.startswith(b"\x89PNG\r\n\x1a\n"), "截图内容与 PNG 类型不符。")
    offset, size, observed, saw_data, ended = 8, len(data), None, False, False
    while offset < size:
        require(size - offset >= 12, "PNG 截图不完整。")
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        require(end <= size, "PNG 截图块长度无效。")
        payload = data[offset + 8:end - 4]
        checksum = struct.unpack_from(">I", data, end - 4)[0]
        require(zlib.crc32(kind + payload) & 0xffffffff == checksum, "PNG 截图校验失败。")
        if observed is None:
            require(kind == b"IHDR" and length == 13, "PNG 截图缺少有效图像头。")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            legal_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            require(depth in legal_depths.get(colour, ()) and compression == filtering == 0 and interlace in (0, 1),
                    "PNG 截图图像头无效。")
            observed = dimensions(width, height)
        elif kind == b"IHDR":
            raise AutomationImageError("PNG 截图包含重复图像头。")
        if kind == b"IDAT":
            saw_data = True
        if kind == b"IEND":
            require(length == 0 and end == size and saw_data, "PNG 截图结尾无效。")
            ended = True
        require(not ended or end == size, "PNG 截图包含多余数据。")
        offset = end
    require(ended and observed is not None, "PNG 截图不完整。")
    return observed


def jpeg_dimensions(data):
    require(data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"), "截图内容与 JPEG 类型不符或不完整。")
    position, observed, scan, entropy = 2, None, False, False
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position < len(data):
        if entropy:
            marker_start = data.find(b"\xff", position)
            require(marker_start >= 0 and marker_start + 1 < len(data), "JPEG 截图扫描不完整。")
            position = marker_start
        require(data[position] == 0xFF, "JPEG 截图标记无效。")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        require(position < len(data), "JPEG 截图标记不完整。")
        marker = data[position]
        position += 1
        if entropy and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue
        entropy = False
        if marker == 0xD9:
            require(position == len(data) and scan and observed is not None, "JPEG 截图结尾无效。")
            return observed
        require(marker not in (0, 0xD8) and not 0xD0 <= marker <= 0xD7,
                "JPEG 截图标记次序无效。")
        if marker == 1:
            continue
        require(position + 2 <= len(data), "JPEG 截图块不完整。")
        length = struct.unpack_from(">H", data, position)[0]
        require(length >= 2 and position + length <= len(data), "JPEG 截图块长度无效。")
        if marker in sof:
            require(length >= 8 and observed is None, "JPEG 截图图像头无效。")
            height, width = struct.unpack_from(">HH", data, position + 3)
            components = data[position + 7]
            require(1 <= components <= 4 and length == 8 + 3 * components, "JPEG 截图颜色分量无效。")
            observed = dimensions(width, height)
        if marker == 0xDA:
            require(observed is not None and length >= 6, "JPEG 截图缺少图像头或扫描参数。")
            scan, entropy = True, True
        position += length
    raise AutomationImageError("JPEG 截图不完整。")


def decode_image(block):
    require(type(block) is dict and block.get("type") == "image", "截图必须包含 MCP 图像内容。")
    mime = block.get("mimeType")
    encoded = block.get("data")
    require(mime in ("image/png", "image/jpeg"), "截图仅支持 PNG 或 JPEG。")
    require(type(encoded) is str and 0 < len(encoded) <= MAX_BASE64_BYTES, "截图编码为空或超出大小限制。")
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError, binascii.Error):
        raise AutomationImageError("截图不是有效的 Base64 编码。") from None
    require(0 < len(data) <= MAX_IMAGE_BYTES, "截图字节为空或超出大小限制。")
    width, height = png_dimensions(data) if mime == "image/png" else jpeg_dimensions(data)
    return data, mime, width, height


def _protected_directory(path):
    # Starting at / and opening each component protects every parent, rather
    # than checking symlinks and later racing a path-based open().
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.path.sep, flags)
    try:
        for part in path.split(os.path.sep):
            if not part:
                continue
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def save_capture(data, mime, width, height):
    require(hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd,
            "当前环境不能安全创建截图目录。")
    cwd = os.getcwd()
    cwd_fd = capture_fd = file_fd = None
    name = str(uuid.uuid4()) + (".png" if mime == "image/png" else ".jpg")
    created = completed = False
    try:
        cwd_fd = _protected_directory(cwd)
        try:
            os.mkdir(CAPTURE_DIR, mode=0o700, dir_fd=cwd_fd)
        except FileExistsError:
            pass
        capture_fd = os.open(CAPTURE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cwd_fd)
        directory = os.fstat(capture_fd)
        require(stat.S_ISDIR(directory.st_mode) and directory.st_uid == os.geteuid()
                and stat.S_IMODE(directory.st_mode) == 0o700, "截图目录的所有者或权限不安全。")
        file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=capture_fd)
        created = True
        os.fchmod(file_fd, 0o600)
        target = os.fstat(file_fd)
        require(stat.S_ISREG(target.st_mode) and target.st_nlink == 1
                and target.st_uid == os.geteuid() and stat.S_IMODE(target.st_mode) == 0o600,
                "截图文件的类型、所有者或权限不安全。")
        with os.fdopen(file_fd, "wb") as output:
            file_fd = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        named = os.stat(CAPTURE_DIR, dir_fd=cwd_fd, follow_symlinks=False)
        require(stat.S_ISDIR(named.st_mode) and (named.st_dev, named.st_ino) == (directory.st_dev, directory.st_ino),
                "截图目录在保存期间发生变化。")
        completed = True
        return {"path": os.path.join(cwd, CAPTURE_DIR, name), "sha256": hashlib.sha256(data).hexdigest(),
                "mimeType": mime, "bytes": len(data), "width": width, "height": height}
    except OSError:
        raise AutomationImageError("无法在受保护的本地目录保存截图。") from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if created and not completed:
            try:
                os.unlink(name, dir_fd=capture_fd)
            except OSError:
                pass
        if capture_fd is not None:
            os.close(capture_fd)
        if cwd_fd is not None:
            os.close(cwd_fd)


def materialize_screenshot(envelope):
    """Replace one screenshot's Base64 with an actual capture and read hint."""
    result = envelope.get("result") if isinstance(envelope, dict) else None
    require(type(result) is dict, "截图缺少 MCP 返回内容。")
    if result.get("isError") is True:
        return envelope
    content = result.get("content")
    require(type(content) is list, "截图缺少 MCP 内容列表。")
    images = [block for block in content if isinstance(block, dict) and block.get("type") == "image"]
    require(len(images) == 1, "截图必须返回一张独立的图像。")
    image = images[0]
    data, mime, width, height = decode_image(image)
    capture = save_capture(data, mime, width, height)
    encoded = image["data"]
    hint = ("截图已保存：%s；SHA-256：%s；尺寸：%d×%d。"
            "请使用 read_image 读取该文件后再判断屏幕内容；当前回执不代表已看过图像。"
            % (capture["path"], capture["sha256"], width, height))

    def replace(value):
        if isinstance(value, dict):
            if value.get("type") == "image":
                return {"type": "text", "text": hint}
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, str):
            return value.replace(encoded, capture["path"])
        return value

    processed = replace(copy.deepcopy(envelope))
    processed["captures"] = [capture]
    return processed
