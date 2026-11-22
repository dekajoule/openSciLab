"""Screen readers: names of icon-only buttons and of the painted views."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from openscilab.driver.models import AnalyzerChannel, CaptureSession
from openscilab.driver.simulated import open_simulated
from openscilab.ui.accessibility import unnamed_buttons


def test_every_icon_only_button_has_a_name(shell, make_dataview):
    shell.show_start_page()
    flow = shell.new_flow()
    flow.add_node("control.timer", (0, 0))
    instrument = open_simulated("uno")
    shell.hub.add(instrument)
    card = shell.open_device_card(instrument)
    view = make_dataview()
    panel = shell.new_panel()
    shell.show_hardware()
    QApplication.processEvents()
    for document in (flow, card, view, panel):
        shell.area.activate(document)
        QApplication.processEvents()
        assert unnamed_buttons(document) == [], document.title
    assert unnamed_buttons(shell) == []


def test_painted_views_describe_themselves(shell, make_dataview):
    flow = shell.new_flow()
    flow.add_node("control.timer", (0, 0))
    QApplication.processEvents()
    assert flow.view.accessibleName() == "Flow graph" and "1 nodes" in flow.view.accessibleDescription()
    view = make_dataview()
    session = CaptureSession(frequency=1000, pre_trigger_samples=0, post_trigger_samples=100)
    session.capture_channels = [AnalyzerChannel(channel_number=0, samples=np.zeros(100, np.uint8))]
    view.load_session(session)
    assert view.sample_viewer.accessibleName() == "Waveform"
    assert "1 channels" in view.sample_viewer.accessibleDescription()


def test_status_dots_are_sharp():
    from openscilab.core.instrument import InstrumentStatus
    from openscilab.ui.documents.device import status_icon

    QApplication.instance() or QApplication([])
    pixmap = status_icon(InstrumentStatus.CONNECTED).pixmap(20, 20)
    assert pixmap.devicePixelRatio() >= 1
    assert status_icon(InstrumentStatus.CONNECTED).availableSizes()[0].width() >= 40


def test_the_capture_settings_are_one_page_and_show_where_a_problem_is(shell):
    from PySide6.QtWidgets import QGroupBox

    from openscilab.ui.dialogs.capture_dialog import CaptureDialog

    instrument = open_simulated("free")
    dialog = CaptureDialog(instrument.capture.driver, shell)
    page = dialog.page_scroll.widget()
    groups = [page.layout().itemAt(index).widget() for index in range(page.layout().count())]
    assert [group.title() for group in groups if isinstance(group, QGroupBox)] == ["Sampling", "Trigger", "Channels"]
    for selector in dialog.channel_selectors:
        selector.enabled = False
    dialog.resize(900, 360)  # (the page scrolls)
    dialog.show()
    bar = dialog.page_scroll.verticalScrollBar()
    bar.setValue(0)
    assert dialog.build_session() is None  # no channel
    assert bar.value() > 0 and "channel" in dialog.validation_label.text().lower()  # scrolled to the channels
    dialog._update_overview()
    assert dialog.overview_label.text().startswith("0 channels")
    dialog.reject()


def test_the_capture_setup_draws_its_capture_on_a_time_line(shell):
    from openscilab.ui.dialogs.capture_dialog import CaptureDialog

    dialog = CaptureDialog(open_simulated("free").capture.driver, shell)
    dialog.frequency_box.setValue(10_000)
    dialog.edge_radio.setChecked(True)
    dialog.pre_samples_box.setValue(500)
    dialog.post_samples_box.setValue(2000)
    plan = dialog.timeline.plan
    assert (plan.pre, plan.post, plan.start) == (500, 2000, "trigger")
    assert dialog.timeline.accessibleDescription() == (
        "before: 500 samples · 50 ms, Trigger, after: 2,000 samples · 200 ms")
    dialog.burst_box.setChecked(True)
    dialog.burst_count_box.setValue(3)
    assert "3 bursts × 2,000 samples" in dialog.timeline.description()
    dialog.immediate_radio.setChecked(True)  # no trigger: nothing before the start
    assert dialog.timeline.plan.pre == 0 and dialog.timeline.description().startswith("Start, after:")
    dialog.timeline.resize(600, dialog.timeline.sizeHint().height())
    assert not dialog.timeline.grab().isNull()
    dialog.reject()


def test_the_capture_is_set_on_the_time_line_within_what_the_device_captures(shell):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest

    from openscilab.ui.dialogs.capture_dialog import CaptureDialog
    from openscilab.ui.widgets.capture_timeline import nice_count, step_count

    assert [step_count(1000, 1), step_count(1000, -1), step_count(2000, 2), step_count(1, -1)] == [2000, 500, 10000, 1]
    assert nice_count(1040) == 1000 and nice_count(1400) == 1500

    dialog = CaptureDialog(open_simulated("free").capture.driver, shell)
    dialog.edge_radio.setChecked(True)
    total = dialog.limits.max_total_samples
    dialog.pre_samples_box.setValue(1000)
    dialog.post_samples_box.setValue(total)  # more than fits: only what does
    assert dialog.post_samples_box.value() == total - 1000
    dialog.burst_box.setChecked(True)
    dialog.burst_count_box.setValue(4)
    assert dialog.post_samples_box.value() == (total - 1000) // 4
    dialog.burst_box.setChecked(False)
    assert dialog.build_session() is not None

    dialog.pre_samples_box.setValue(1000)
    dialog.post_samples_box.setValue(3000)
    timeline = dialog.timeline
    timeline.resize(800, timeline.sizeHint().height())
    assert timeline.plan.movable and timeline.plan.resizable
    bar = timeline._bar()
    middle = QPoint(int(bar.center().x()), int(bar.center().y()))
    QTest.mouseClick(timeline, Qt.LeftButton, Qt.NoModifier, middle)  # the trigger in the middle
    assert (dialog.pre_samples_box.value(), dialog.post_samples_box.value()) == (2000, 2000)
    timeline.setFocus()
    QTest.keyClick(timeline, Qt.Key_Up)  # longer, the trigger stays in the middle
    assert (dialog.pre_samples_box.value(), dialog.post_samples_box.value()) == (2500, 2500)  # (4000 → 5000)
    QTest.keyClick(timeline, Qt.Key_Left)
    assert dialog.pre_samples_box.value() == 2450

    dialog.immediate_radio.setChecked(True)  # without a trigger nothing comes before it
    assert not timeline.plan.movable
    timeline._request(0.5, 4000)
    assert dialog.post_samples_box.value() == 4000
    dialog.reject()
