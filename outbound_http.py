"""Validate outbound web requests and follow redirects one hop at a time."""

import ipaddress
import re
import socket
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit

import urllib3


MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class OutboundRequestError(ValueError):
    pass


def parse_web_url(url, *, allowed_host=None, assume_https=False):
    if not isinstance(url, str) or not url or re.search(r"[\\\x00-\x20\x7f]", url):
        raise OutboundRequestError("Invalid URL")
    if assume_https and "://" not in url:
        url = f"https://{url}"

    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in {"http", "https"} or not host or "@" in parts.netloc:
        raise OutboundRequestError("Expected an HTTP(S) URL without credentials")
    if parts.port not in {None, 443 if parts.scheme == "https" else 80}:
        raise OutboundRequestError("Non-standard HTTP port")
    if "%" in host or (allowed_host is not None and not allowed_host(host)):
        raise OutboundRequestError("Host is outside the allowed scope")
    return parts


def resolve_public_addresses(host, port):
    try:
        addresses = {
            ipaddress.ip_address(entry[4][0])
            for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise OutboundRequestError("Could not resolve public host") from exc
    for address in addresses:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise OutboundRequestError("Host resolves to a non-public address")
    if not addresses:
        raise OutboundRequestError("Host did not resolve")
    return sorted(addresses, key=lambda address: (address.version, int(address)))


def validate_public_url(url, *, allowed_host=None):
    parts = parse_web_url(url, allowed_host=allowed_host)
    resolve_public_addresses(parts.hostname, 443 if parts.scheme == "https" else 80)
    return parts


@contextmanager
def open_public_url(url, *, allowed_host=None, timeout=(8, 25), user_agent="Telegram Social Media Downloader Bot"):
    for hop in range(MAX_REDIRECTS + 1):
        parts = parse_web_url(url, allowed_host=allowed_host)
        secure = parts.scheme == "https"
        port = 443 if secure else 80
        hostname = parts.hostname.encode("idna").decode("ascii")
        addresses = resolve_public_addresses(hostname, port)
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"

        # Connect to an address we just validated. Preserve the hostname for TLS and HTTP.
        for index, address in enumerate(addresses):
            pool = (
                urllib3.HTTPSConnectionPool(
                    str(address), port, server_hostname=hostname,
                    assert_hostname=hostname, cert_reqs="CERT_REQUIRED",
                )
                if secure else urllib3.HTTPConnectionPool(str(address), port)
            )
            try:
                response = pool.urlopen(
                    "GET", target, headers={"Host": host_header, "User-Agent": user_agent},
                    assert_same_host=False, timeout=urllib3.Timeout(connect=timeout[0], read=timeout[1]),
                    redirect=False, retries=False, preload_content=False,
                )
                break
            except (urllib3.exceptions.NewConnectionError, urllib3.exceptions.ConnectTimeoutError):
                pool.close()
                if index == len(addresses) - 1:
                    raise
            except BaseException:
                pool.close()
                raise

        try:
            if 200 <= response.status < 300:
                yield response
                return
            location = response.headers.get("Location")
            if response.status not in REDIRECT_STATUSES or not location:
                raise OutboundRequestError(f"Unexpected HTTP status {response.status}")
            if hop == MAX_REDIRECTS:
                raise OutboundRequestError("Too many redirects")
            if re.search(r"[\\\x00-\x20\x7f]", location):
                raise OutboundRequestError("Invalid redirect URL")
            url = urljoin(parts.geturl(), location)
        finally:
            response.close()
            pool.close()
