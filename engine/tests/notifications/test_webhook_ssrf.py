import pytest

from veridian_engine.notifications.webhook import UnsafeUrlError, check_url


def resolver_for(*addresses):
    def resolve(host, port):
        return list(addresses)

    return resolve


def test_public_https_url_is_accepted():
    ips = check_url("https://hooks.example.com/veridian", resolver=resolver_for("93.184.216.34"))
    assert [str(ip) for ip in ips] == ["93.184.216.34"]


@pytest.mark.parametrize("url", ["http://hooks.example.com/x", "ftp://hooks.example.com", "hooks.example.com"])
def test_non_https_is_rejected(url):
    with pytest.raises(UnsafeUrlError):
        check_url(url, resolver=resolver_for("93.184.216.34"))


def test_credentials_in_url_are_rejected():
    with pytest.raises(UnsafeUrlError):
        check_url("https://user:pass@hooks.example.com/", resolver=resolver_for("93.184.216.34"))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "172.16.3.4",  # private
        "192.168.1.1",  # private
        "169.254.169.254",  # link-local / cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "255.255.255.255",  # broadcast
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique local
        "ff02::1",  # IPv6 multicast
        "::",  # IPv6 unspecified
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",  # IPv4-mapped metadata
        "fd00:ec2::254",  # AWS IPv6 metadata
    ],
)
def test_non_public_addresses_are_rejected(address):
    with pytest.raises(UnsafeUrlError):
        check_url("https://hooks.example.com/", resolver=resolver_for(address))


def test_any_bad_address_among_many_rejects():
    with pytest.raises(UnsafeUrlError):
        check_url("https://hooks.example.com/", resolver=resolver_for("93.184.216.34", "10.0.0.1"))


def test_ip_literal_host_is_checked_without_resolver():
    with pytest.raises(UnsafeUrlError):
        check_url("https://127.0.0.1/hook", resolver=resolver_for("93.184.216.34"))
    with pytest.raises(UnsafeUrlError):
        check_url("https://[::1]/hook", resolver=resolver_for("93.184.216.34"))


def test_unresolvable_host_is_rejected():
    def fail(host, port):
        raise OSError("no such host")

    with pytest.raises(UnsafeUrlError):
        check_url("https://nope.invalid/", resolver=fail)
    with pytest.raises(UnsafeUrlError):
        check_url("https://hooks.example.com/", resolver=resolver_for())
