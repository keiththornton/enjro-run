import pytest

from strixrun.binaries.manager import BinaryError, Manifest, detect_variant


class Probe:
    def __init__(self, system, machine, driver=None):
        self.system, self.machine, self.driver = system, machine, driver

    def platform(self):
        return self.system, self.machine

    def nvidia_driver_version(self):
        return self.driver


@pytest.mark.parametrize("system,machine,driver,expected", [
    ("Darwin", "arm64", None, "macos-arm64"),
    ("Darwin", "x86_64", None, "macos-x64"),
    ("Windows", "AMD64", None, "win-cpu-x64"),
    ("Windows", "AMD64", (581, 15), "win-cuda-13-x64"),
    ("Windows", "AMD64", (560, 94), "win-cuda-12-x64"),
    ("Windows", "AMD64", (512, 0), "win-cpu-x64"),   # too old for CUDA 12.4
    ("Linux", "x86_64", (581, 0), "linux-cpu-x64"),  # Linux GPU builds are out of scope for v1
])
def test_detect_variant(system, machine, driver, expected):
    assert detect_variant(Probe(system, machine, driver)) == expected


def test_unsupported_platform():
    with pytest.raises(BinaryError, match="no prebuilt"):
        detect_variant(Probe("Linux", "riscv64"))


def test_manifest_lookup_default_tag():
    m = Manifest.load()
    spec = m.lookup(None, "macos-arm64")
    assert spec.tag == m.default
    assert spec.asset.endswith("macos-arm64.tar.gz") and spec.format == "tar.gz"
    assert len(spec.sha256) == 64
    assert spec.url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/")


def test_cuda_variant_carries_cudart_extra():
    spec = Manifest.load().lookup(None, "win-cuda-12-x64")
    assert [e.asset for e in spec.extras] == ["cudart-llama-bin-win-cuda-12.4-x64.zip"]


def test_unknown_tag_or_variant_rejected():
    m = Manifest.load()
    with pytest.raises(BinaryError, match="not in the manifest"):
        m.lookup("b1", "macos-arm64")
    with pytest.raises(BinaryError, match="no build"):
        m.lookup(None, "plan9-x64")


def test_manifest_urls_are_github_https_only():
    m = Manifest.load()
    for tag in m.tags():
        for variant in m.variants(tag):
            spec = m.lookup(tag, variant)
            for e in [spec, *spec.extras]:
                assert e.url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/")
