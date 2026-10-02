import sys
import socket
import struct
import time
import json
import traceback
import pyqtgraph as pg
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QProgressBar, QPushButton, 
                             QSlider, QGroupBox, QGridLayout, QTabWidget, QComboBox,
                             QMessageBox, QFileDialog, QCheckBox)
from PyQt6.QtCore import QTimer, Qt
import math
import os           
import ctypes
from PyQt6.QtGui import QIcon

# Global Exception Hook to prevent silent PyQt crashes
def exception_hook(exctype, value, tb):
    print("CRITICAL UI ERROR:")
    print("".join(traceback.format_exception(exctype, value, tb)))
sys.excepthook = exception_hook

BOARD_IP = "192.168.1.100"
BOARD_PORT = 6969

# Tell Windows to use our icon on the taskbar, not the generic Python one
myappid = 'simrigmaster.calibration.suite.1.0'
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class ProductionCalibrationUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sim Rig Master Calibration Suite (Production)")
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.setGeometry(50, 50, 1100, 800)
        
        # Will be overwritten by board telemetry on first connection
        self.calib = {}
        
        self.latest_pkt = None
        self.last_packet_time = 0
        self.smoothed_angle = None
        self.smoothed_hfused = None

        self.synced_with_board = False
        self.unsaved_changes = False
        self.is_syncing_ui = False

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)

        self.init_ui()

        # High-frequency network timer (100Hz polling)
        self.net_timer = QTimer(self)
        self.net_timer.setInterval(10)
        self.net_timer.timeout.connect(self.poll_network)
        self.net_timer.start()

        # Smooth GUI Render timer (30Hz)
        self.gui_timer = QTimer(self)
        self.gui_timer.setInterval(33) 
        self.gui_timer.timeout.connect(self.update_gui)
        self.gui_timer.start()

        self.last_ping = 0

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        # Header Status Bar & Sync State
        header_layout = QHBoxLayout()
        self.status_bar = QLabel("STATUS: OFFLINE")
        self.status_bar.setStyleSheet("background-color: #333; color: orange; font-size: 14px; font-weight: bold; padding: 8px;")
        
        self.sync_badge = QLabel("SYNC STATE: WAITING FOR BOARD...")
        self.sync_badge.setStyleSheet("background-color: #424242; color: white; font-size: 14px; font-weight: bold; padding: 8px;")
        
        header_layout.addWidget(self.status_bar, 2)
        header_layout.addWidget(self.sync_badge, 1)
        main_layout.addLayout(header_layout)

        # Profile Management
        prof_layout = QHBoxLayout()
        btn_export = QPushButton("Export Profile (JSON)")
        btn_export.clicked.connect(self.export_profile)
        btn_import = QPushButton("Import Profile (JSON)")
        btn_import.clicked.connect(self.import_profile)
        prof_layout.addWidget(btn_export)
        prof_layout.addWidget(btn_import)
        main_layout.addLayout(prof_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_pedals_tab(), "Pedals")
        self.tabs.addTab(self.create_gearbox_tab(), "Gearbox")
        self.tabs.addTab(self.create_tillers_tab(), "Tillers")
        main_layout.addWidget(self.tabs)

        cmd_layout = QHBoxLayout()
        
        # Styled Buttons
        self.save_btn = QPushButton("FLASH WRITE (PERSIST TO FLASH)")
        self.save_btn.setStyleSheet("""
            QPushButton { background-color: #2e7d32; color: white; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #388e3c; }
            QPushButton:pressed { background-color: #1b5e20; }
        """)
        self.save_btn.clicked.connect(self.save_to_flash)

        self.reset_btn = QPushButton("RESET DEFAULTS")
        self.reset_btn.setStyleSheet("""
            QPushButton { background-color: #c62828; color: white; font-weight: bold; font-size: 14px; padding: 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #d32f2f; }
            QPushButton:pressed { background-color: #b71c1c; }
        """)
        self.reset_btn.clicked.connect(self.reset_defaults)

        cmd_layout.addWidget(self.save_btn, 2)
        cmd_layout.addWidget(self.reset_btn, 1)
        main_layout.addLayout(cmd_layout)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        self.update_controls_state()

    def update_controls_state(self):
        enabled = self.synced_with_board
        self.tabs.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)

    def set_unsaved_changes(self, changed: bool):
        self.unsaved_changes = changed
        if changed:
            self.sync_badge.setText("SYNC STATE: UNSAVED CHANGES (IN RAM)")
            self.sync_badge.setStyleSheet("background-color: #ef6c00; color: white; font-size: 14px; font-weight: bold; padding: 8px;")
        else:
            self.sync_badge.setText("SYNC STATE: SAVED TO FLASH")
            self.sync_badge.setStyleSheet("background-color: #1565c0; color: white; font-size: 14px; font-weight: bold; padding: 8px;")

    # ------------------- TAB CREATION -------------------
    def create_pedals_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        self.pedal_bars = {}
        self.pedal_raw_labels = {}

        for pedal, name in [('accel', 'Throttle'), ('clutch', 'Clutch'), ('brake', 'Brake Hybrid')]:
            box = QGroupBox(f"{name} Control")
            vbox = QVBoxLayout()
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            vbox.addWidget(pbar)
            raw_lbl = QLabel("Raw Input: --")
            vbox.addWidget(raw_lbl)

            btn_hbox = QHBoxLayout()
            btn_min = QPushButton(f"Set {name} Min")
            btn_max = QPushButton(f"Set {name} Max")
            btn_min.clicked.connect(lambda _, p=pedal: self.capture_pedal_limit(p, 'min'))
            btn_max.clicked.connect(lambda _, p=pedal: self.capture_pedal_limit(p, 'max'))
            btn_hbox.addWidget(btn_min)
            btn_hbox.addWidget(btn_max)
            
            vbox.addLayout(btn_hbox)
            box.setLayout(vbox)
            layout.addWidget(box)
            self.pedal_bars[pedal] = pbar
            self.pedal_raw_labels[pedal] = raw_lbl

        blend_box = QGroupBox("Brake Trust Blender")
        bv = QVBoxLayout()
        self.trust_lbl = QLabel("Blend: 50% Hall / 50% Load Cell")
        self.trust_slider = QSlider(Qt.Orientation.Horizontal)
        self.trust_slider.setRange(0, 100)
        self.trust_slider.valueChanged.connect(self.on_trust_slider_change)
        bv.addWidget(self.trust_lbl)
        bv.addWidget(self.trust_slider)
        blend_box.setLayout(bv)
        layout.addWidget(blend_box)
        tab.setLayout(layout)
        return tab

    def create_gearbox_tab(self):
        tab = QWidget()
        layout = QHBoxLayout()
        controls_layout = QVBoxLayout()
        
        telemetry_box = QGroupBox("Live Shifter Position")
        tb_layout = QVBoxLayout()
        self.lbl_active_gear = QLabel("Active Gear: NEUTRAL")
        self.lbl_active_gear.setStyleSheet("font-size: 20px; font-weight: bold; color: cyan;")
        self.lbl_gangle = QLabel("X-Axis (Angle): --")
        self.lbl_hfused = QLabel("Y-Axis (H_Fused): --")
        tb_layout.addWidget(self.lbl_active_gear)
        tb_layout.addWidget(self.lbl_gangle)
        tb_layout.addWidget(self.lbl_hfused)

        self.sensor_mode_combo = QComboBox()
        self.sensor_mode_combo.addItems(["Use H1 & H2", "Use H1 Only", "Use H2 Only"])
        self.sensor_mode_combo.currentIndexChanged.connect(self.on_h_sensor_mode_changed)
        tb_layout.addWidget(QLabel("<b>Y-Axis Hardware Mode:</b>"))
        tb_layout.addWidget(self.sensor_mode_combo)
        
        telemetry_box.setLayout(tb_layout)
        controls_layout.addWidget(telemetry_box)
        controls_layout.addStretch()

        self.plot_widget = pg.PlotWidget(title="Live 2D H-Pattern Gate Mapping")
        
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.plotItem.invertX(True)
        self.plot_widget.plotItem.invertY(True)
        self.stick_marker = self.plot_widget.plot([0], [0], pen=None, symbol='o', symbolSize=18, symbolBrush='cyan')

        lbl_opts = {'position': 0.85, 'color': (255, 255, 255), 'fill': (0, 0, 0, 150), 'movable': True}
        hover_pen = pg.mkPen('w', width=5)

        self.line_col_left = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen('b', width=3), hoverPen=hover_pen, label='L', labelOpts=lbl_opts)
        self.line_col_right = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen('g', width=3), hoverPen=hover_pen, label='R', labelOpts=lbl_opts)
        self.line_row_top = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen('y', width=3), hoverPen=hover_pen, label='T', labelOpts=lbl_opts)
        self.line_row_bot = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen('m', width=3), hoverPen=hover_pen, label='B', labelOpts=lbl_opts)

        for line in [self.line_col_left, self.line_col_right, self.line_row_top, self.line_row_bot]:
            self.plot_widget.addItem(line)
            line.sigPositionChanged.connect(self.on_gate_line_dragged)

        layout.addLayout(controls_layout, 1)
        layout.addWidget(self.plot_widget, 3)
        tab.setLayout(layout)
        return tab

    def create_tillers_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        self.tiller_bars = {}
        self.tiller_raw_lbls = {}
        self.tiller_inverts = {}

        for stick, name, prefix in [('l_stick', 'Left Tiller', 'ls'), ('r_stick', 'Right Tiller', 'rs')]:
            box = QGroupBox(name)
            vbox = QVBoxLayout()
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            vbox.addWidget(pbar)
            
            lbl_raw = QLabel("Raw Counter: 0")
            vbox.addWidget(lbl_raw)

            # Invert Control Only
            hz_controls = QHBoxLayout()
            inv_cb = QCheckBox("Invert (Reverse Direction)")
            inv_cb.stateChanged.connect(lambda state, p=prefix: self.on_tiller_invert(p, state))
            self.tiller_inverts[stick] = inv_cb
            
            hz_controls.addWidget(inv_cb)
            hz_controls.addStretch()
            vbox.addLayout(hz_controls)

            # Min / Max Set Buttons
            btn_hbox = QHBoxLayout()
            btn_min = QPushButton("Set Min")
            btn_max = QPushButton("Set Max")
            btn_min.clicked.connect(lambda _, s=stick: self.capture_tiller_limit(s, 'min'))
            btn_max.clicked.connect(lambda _, s=stick: self.capture_tiller_limit(s, 'max'))
            
            btn_hbox.addWidget(btn_min)
            btn_hbox.addWidget(btn_max)
            vbox.addLayout(btn_hbox)
            
            box.setLayout(vbox)
            layout.addWidget(box)
            
            self.tiller_bars[stick] = pbar
            self.tiller_raw_lbls[stick] = lbl_raw
            
        tab.setLayout(layout)
        return tab

    # ------------------- NETWORK & SYNC -------------------
    # ------------------- NETWORK & SYNC -------------------
    # ------------------- NETWORK & SYNC -------------------
    def poll_network(self):
        now = time.time()
        if now - self.last_ping > 0.8:
            try: self.sock.sendto(b'\x01', (BOARD_IP, BOARD_PORT))
            except: pass
            self.last_ping = now

        latest_data = None
        while True:
            try:
                data, _ = self.sock.recvfrom(2048)
                latest_data = data
            except (BlockingIOError, socket.error):
                break
            
        # Expecting exactly 180 bytes now
        if latest_data is not None and len(latest_data) >= 180: 
            self.last_packet_time = now
            self.status_bar.setText("STATUS: ONLINE (100Hz Telemetry)")
            self.status_bar.setStyleSheet("background-color: #2e7d32; color: white; font-size: 14px; font-weight: bold; padding: 8px;")

            try:
                # 1. Parse Base Telemetry and Scrub NaNs
                t_raw = struct.unpack('<5f 4i 3f 1i 2I 1i 1f 1i 2f 3i', latest_data[:92])
                t_up = [0.0 if (isinstance(x, float) and math.isnan(x)) else x for x in t_raw]
                
                self.latest_pkt = {
                    'accel': t_up[2], 'brake': t_up[3], 'clutch': t_up[4],
                    'r_stick': t_up[0], 'l_stick': t_up[1], 'r_raw': t_up[6], 'l_raw': t_up[7],
                    'th_angle_raw': t_up[9], 'cl_angle_raw': t_up[10], 'br_angle_raw': t_up[11], 'br_lc_raw': t_up[12],
                    'h1_adc': t_up[13], 'h2_adc': t_up[14], 'h_fused': t_up[15], 'gear_angle_raw': t_up[16], 'raw_gear': t_up[17]
                }
                
                # 2. Parse Board Config & Scrub NaNs 
                if not self.synced_with_board:
                    # Added the 'I' at the beginning to swallow the 4-byte Magic Key
                    c_raw = struct.unpack('<I 6f 2i f 4i 2f 5i', latest_data[92:176])
                    c_up = [0.0 if (isinstance(x, float) and math.isnan(x)) else x for x in c_raw]
                    
                    self.calib = {
                        'th_min': c_up[1], 'th_max': c_up[2], 'cl_min': c_up[3], 'cl_max': c_up[4],
                        'br_hall_min': c_up[5], 'br_hall_max': c_up[6], 'br_lc_min': c_up[7], 'br_lc_max': c_up[8],
                        'br_lc_trust': c_up[9], 'rs_min': c_up[10], 'rs_max': c_up[11], 'ls_min': c_up[12], 'ls_max': c_up[13],
                        'gear_col_left': c_up[14], 'gear_col_right': c_up[15], 'gear_row_top': c_up[16], 'gear_row_bot': c_up[17],
                        'h_sensor_mode': c_up[18], 'ls_invert': c_up[19], 'rs_invert': c_up[20]
                    }
                    self.sync_ui_to_dict()
                    self.synced_with_board = True
                    self.set_unsaved_changes(False)
                    self.update_controls_state()

            except Exception as e:
                print(f"Unpack error: {e}")

        elif now - self.last_packet_time > 2.0:
            self.status_bar.setText("STATUS: OFFLINE")
            self.status_bar.setStyleSheet("background-color: #c62828; color: white; font-size: 14px; font-weight: bold; padding: 8px;")
            self.synced_with_board = False
            self.update_controls_state()

    def sync_ui_to_dict(self):
        """Silently sets all UI widgets to match self.calib without triggering change events"""
        self.is_syncing_ui = True
        
        self.trust_slider.blockSignals(True)
        self.trust_slider.setValue(int(self.calib.get('br_lc_trust', 0.5) * 100))
        self.trust_slider.blockSignals(False)

        self.sensor_mode_combo.blockSignals(True)
        self.sensor_mode_combo.setCurrentIndex(self.calib.get('h_sensor_mode', 0))
        self.sensor_mode_combo.blockSignals(False)

        self.line_col_left.setValue(self.calib.get('gear_col_left', -58.0))
        self.line_col_right.setValue(self.calib.get('gear_col_right', -48.0))
        self.line_row_top.setValue(self.calib.get('gear_row_top', -1500))
        self.line_row_bot.setValue(self.calib.get('gear_row_bot', 1500))

        # Tiller Inverts
        for stick, prefix in [('l_stick', 'ls'), ('r_stick', 'rs')]:
            self.tiller_inverts[stick].blockSignals(True)
            self.tiller_inverts[stick].setChecked(bool(self.calib.get(f"{prefix}_invert", 0)))
            self.tiller_inverts[stick].blockSignals(False)

        # Center camera over current setup
        if self.latest_pkt:
            c_ang = self.latest_pkt['gear_angle_raw']
            c_fus = self.latest_pkt['h_fused']
            self.plot_widget.setXRange(c_ang - 40, c_ang + 40)
            y_span = 1000 if self.calib.get('h_sensor_mode', 0) > 0 else 2500
            self.plot_widget.setYRange(c_fus - y_span, c_fus + y_span)

        self.is_syncing_ui = False

    def update_gui(self):
        if not self.latest_pkt or not self.synced_with_board: return
        pkt = self.latest_pkt

        # EMA Visual Filter
        alpha = 0.15
        if self.smoothed_angle is None:
            self.smoothed_angle, self.smoothed_hfused = pkt['gear_angle_raw'], pkt['h_fused']
        else:
            self.smoothed_angle += (pkt['gear_angle_raw'] - self.smoothed_angle) * alpha
            self.smoothed_hfused += (pkt['h_fused'] - self.smoothed_hfused) * alpha

        self.pedal_bars['accel'].setValue(max(0, min(100, int(pkt['accel'] * 100))))
        self.pedal_bars['clutch'].setValue(max(0, min(100, int(pkt['clutch'] * 100))))
        self.pedal_bars['brake'].setValue(max(0, min(100, int(pkt['brake'] * 100))))
        
        self.pedal_raw_labels['accel'].setText(f"Raw: {pkt['th_angle_raw']:.2f}°")
        self.pedal_raw_labels['clutch'].setText(f"Raw: {pkt['cl_angle_raw']:.2f}°")
        self.pedal_raw_labels['brake'].setText(f"Hall: {pkt['br_angle_raw']:.2f}° | LoadCell: {pkt['br_lc_raw']}")

        
        self.lbl_hfused.setText(f"Y-Axis (H_Fused): {int(self.smoothed_hfused)}")
        self.lbl_gangle.setText(f"X-Axis (Angle): {self.smoothed_angle:.2f}°")
        self.lbl_active_gear.setText(f"Active Gear: {pkt['raw_gear']}")
        
        # 2D marker
        self.stick_marker.setData([self.smoothed_angle], [self.smoothed_hfused])
        
        self.tiller_bars['l_stick'].setValue(max(0, min(100, int(pkt['l_stick'] * 100))))
        self.tiller_bars['r_stick'].setValue(max(0, min(100, int(pkt['r_stick'] * 100))))
        self.tiller_raw_lbls['l_stick'].setText(f"Raw: {pkt['l_raw']}")
        self.tiller_raw_lbls['r_stick'].setText(f"Raw: {pkt['r_raw']}")

    # ------------------- ACTIONS & PROFILES -------------------
    def export_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Calibration", "", "JSON Files (*.json)")
        if path:
            with open(path, 'w') as f:
                json.dump(self.calib, f, indent=4)

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Calibration", "", "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                    self.calib.update(data)
                    self.sync_ui_to_dict()
                    self.set_unsaved_changes(True)
                    self.send_calib_packet(0x01) 
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load profile: {e}")

    def on_h_sensor_mode_changed(self, idx):
        if self.is_syncing_ui: return
        self.calib['h_sensor_mode'] = idx
        self.set_unsaved_changes(True)
        self.send_calib_packet(0x01)

    def on_gate_line_dragged(self):
        if self.is_syncing_ui: return
        self.calib['gear_col_left'] = self.line_col_left.value()
        self.calib['gear_col_right'] = self.line_col_right.value()
        self.calib['gear_row_top'] = int(self.line_row_top.value())
        self.calib['gear_row_bot'] = int(self.line_row_bot.value())
        self.set_unsaved_changes(True)
        self.send_calib_packet(0x01)

    def capture_pedal_limit(self, pedal, limit_type):
        if not self.latest_pkt: return
        val = self.latest_pkt['th_angle_raw'] if pedal == 'accel' else self.latest_pkt['cl_angle_raw']
        if pedal == 'brake':
            val_h, val_lc = self.latest_pkt['br_angle_raw'], self.latest_pkt['br_lc_raw']
            if limit_type == 'min':
                self.calib['br_hall_min'], self.calib['br_lc_min'] = val_h, val_lc
            else:
                self.calib['br_hall_max'], self.calib['br_lc_max'] = val_h, val_lc
        else:
            key = f"{'th' if pedal == 'accel' else 'cl'}_{limit_type}"
            self.calib[key] = val

        self.set_unsaved_changes(True)
        self.send_calib_packet(0x01)

    def capture_tiller_limit(self, stick, limit_type):
        if not self.latest_pkt: return
        self.calib[f"{'ls' if stick == 'l_stick' else 'rs'}_{limit_type}"] = self.latest_pkt['l_raw'] if stick == 'l_stick' else self.latest_pkt['r_raw']
        self.set_unsaved_changes(True)
        self.send_calib_packet(0x01)
        
    def on_tiller_invert(self, prefix, state):
        if self.is_syncing_ui: return
        self.calib[f"{prefix}_invert"] = 1 if state else 0
        self.set_unsaved_changes(True)
        self.send_calib_packet(0x01)

    def on_trust_slider_change(self, val):
        if self.is_syncing_ui: return
        self.calib['br_lc_trust'] = val / 100.0
        self.trust_lbl.setText(f"Blend: {100-val}% Hall / {val}% Load Cell")
        self.set_unsaved_changes(True)
        self.send_calib_packet(0x01)

    def save_to_flash(self):
        self.send_calib_packet(0x02)
        self.set_unsaved_changes(False)

    def reset_defaults(self):
        reply = QMessageBox.question(self, 'Confirm Reset', 'Are you sure you want to revert all calibrations to firmware defaults?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.send_calib_packet(0x03)
            self.synced_with_board = False 
            self.update_controls_state()

    def send_calib_packet(self, cmd_type):
        if not self.synced_with_board and cmd_type != 0x03:
            return 
        
        c = self.calib
        try:
            # <B  = 1 byte (Command Type)
            # 3x  = 3 empty padding bytes so the C struct aligns perfectly
            # I   = 4 byte (Magic Key)
            # Total size is now exactly 88 bytes, which the C firmware expects!
            packed = struct.pack(
                '<B3xI6f2if4i2f5i',
                cmd_type,
                0xCAFE1234, # Magic Key
                c.get('th_min', 0), c.get('th_max', 0), 
                c.get('cl_min', 0), c.get('cl_max', 0), 
                c.get('br_hall_min', 0), c.get('br_hall_max', 0),
                int(c.get('br_lc_min', 0)), int(c.get('br_lc_max', 0)), 
                c.get('br_lc_trust', 0),
                int(c.get('rs_min', 0)), int(c.get('rs_max', 0)), 
                int(c.get('ls_min', 0)), int(c.get('ls_max', 0)),
                float(min(c.get('gear_col_left', 0), c.get('gear_col_right', 0))), 
                float(max(c.get('gear_col_left', 0), c.get('gear_col_right', 0))),
                int(min(c.get('gear_row_top', 0), c.get('gear_row_bot', 0))), 
                int(max(c.get('gear_row_top', 0), c.get('gear_row_bot', 0))),
                int(c.get('h_sensor_mode', 0)),
                int(c.get('ls_invert', 0)), 
                int(c.get('rs_invert', 0))
            )
            self.sock.sendto(packed, (BOARD_IP, BOARD_PORT))
        except Exception as e:
            print(f"UDP Pack Error: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProductionCalibrationUI()
    window.show()
    sys.exit(app.exec())