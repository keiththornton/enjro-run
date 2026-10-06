import pytest

from strixrun.binaries.manager import BinaryError, Manifest, detect_variant


class Probe:
    def __init__(self, system, machine, driver=None, caps=()):
        self.system, self.machine, self.driver, self.caps = system, machine, driver, list(caps)

    def platform(self):
        return self.system, self.machine

    def nvidia_driver_version(self):
        return self.driver

    def nvidia_compute_caps(self):
        return self.caps


@pytest.mark.parametrize("system,machine,driver,expected", [
    ("Darwin", "arm64", None, "macos-arm64"),
    ("Darwin", "x86_64", None, "macos-x64"),
    ("Windows", "AMD64", None, "win-cpu-x64"),
    ("Windows", "AMD64", (581, 15), "win-cuda-13-x64"),
    ("Windows", "AMD64", (560, 94), "win-cuda-12-x64"),
    ("Windows", "AMD64", (512, 0), "win-cpu-x64"),   # too old for CUDA 12.4
    ("Linux", "x86_64", None, "linux-cpu-x64"),
    ("Linux", "x86_64", (581, 0), "linux-cuda-13-x64"),
    ("Linux", "x86_64", (570, 172), "linux-cuda-12-x64"),
    ("Linux", "x86_64", (525, 60), "linux-cuda-12-x64"),  # CUDA 12 minor-version compatibility
    ("Linux", "x86_64", (470, 0), "linux-cpu-x64"),
])
def test_detect_variant(system, machine, driver, expected):
    assert detect_variant(Probe(system, machine, driver)) == expected


@pytest.mark.parametrize("system,prefix", [("Linux", "linux"), ("Windows", "win")])
def test_older_gpu_keeps_cuda_12(system, prefix):
    # CUDA 13 dropped compute capability below 7.5 (e.g. a GTX 1080 next to an RTX 3090)
    assert detect_variant(Probe(system, "x86_64", (581, 0), [(8, 6), (6, 1)])) == f"{prefix}-cuda-12-x64"
    assert detect_variant(Probe(system, "x86_64", (581, 0), [(8, 6), (7, 5)])) == f"{prefix}-cuda-13-x64"


def test_compute_caps_parsed_from_nvidia_smi(monkeypatch):
    from strixrun.binaries import manager

    monkeypatch.setattr(manager.PlatformProbe, "_nvidia_smi", staticmethod(lambda q: "8.6\n6.1\n"))
    assert manager.PlatformProbe()._query_caps() == [(8, 6), (6, 1)]


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
    linux = Manifest.load().lookup(None, "linux-cuda-12-x64")
    assert linux.asset.endswith("ubuntu-cuda-12.8-x64.tar.gz")
    assert [e.asset for e in linux.extras] == [f"cudart-llama-{linux.tag}-bin-ubuntu-cuda-12.8-x64.tar.gz"]


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
