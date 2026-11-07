"""Controlled font-family selection, not a native macOS/Windows rendering test."""
from types import SimpleNamespace

import pytest

from smm import plots


@pytest.fixture(autouse=True)
def isolated_plot_settings():
    with plots.plt.rc_context():
        plots.plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
        yield


def available_fonts(monkeypatch, *names):
    monkeypatch.setattr(plots.font_manager.fontManager, 'ttflist',
                        [SimpleNamespace(name=name) for name in names])


@pytest.mark.parametrize('family', [
    'PingFang SC', 'PingFang HK', 'Heiti SC', 'Heiti TC', 'STHeiti',
    'Songti SC', 'Microsoft YaHei', 'Microsoft YaHei UI', 'SimSun',
    'Noto Sans CJK SC', 'Noto Sans CJK JP', 'WenQuanYi Zen Hei',
    'WenQuanYi Micro Hei', 'SimHei', 'AR PL UMing CN',
])
def test_selects_available_chinese_family(monkeypatch, family):
    available_fonts(monkeypatch, family)
    plots._setup_font()
    assert plots.plt.rcParams['font.sans-serif'] == [family, 'DejaVu Sans']
    assert plots.plt.rcParams['axes.unicode_minus'] is False


def test_preserves_existing_font_priority(monkeypatch):
    available_fonts(monkeypatch, 'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC')
    plots._setup_font()
    assert plots.plt.rcParams['font.sans-serif'][0] == 'Noto Sans CJK SC'


def test_uses_registered_family_spelling(monkeypatch):
    available_fonts(monkeypatch, 'pingfang sc')
    plots._setup_font()
    assert plots.plt.rcParams['font.sans-serif'] == ['pingfang sc', 'DejaVu Sans']


@pytest.mark.parametrize('names', [(), ('DejaVu Sans',), ('PingFang SC Alternative',)])
def test_does_not_select_missing_or_partial_family(monkeypatch, names):
    available_fonts(monkeypatch, *names)
    plots._setup_font()
    assert plots.plt.rcParams['font.sans-serif'] == ['DejaVu Sans']
    assert plots.plt.rcParams['axes.unicode_minus'] is False
