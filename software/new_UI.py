import sys
import os
import ctypes
import socket
import struct
import time
import json
import math
import traceback
import pyqtgraph as pg
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QProgressBar, QPushButton, 
                             QSlider, QGroupBox, QTabWidget, QComboBox,
                             QMessageBox, QFileDialog, QCheckBox)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QIcon, QColor

def exception_hook(exctype, value, tb):
    print("CRITICAL UI ERROR:")
    print("".join(traceback.format_exception(exctype, value, tb)))
sys.excepthook = exception_hook

BOARD_IP = "192.168.1.100"
BOARD_PORT = 6969
CALIB_MAGIC = 0xCAFE1234          # must equal CALIB_MAGIC_KEY in the firmware
CMD_FMT = '<BI6f2if4i2f5i'        # cmd(1) + magic(4) + config(80) = 85 bytes
assert struct.calcsize(CMD_FMT) == 85, "Command packet layout changed"

# Taskbar icon fix for Windows
myappid = 'simrigmaster.calibration.suite.4.0'
try: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except: pass

def resource_path(relative_path):
    try: base_path = sys._MEIPASS
    except Exception: base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- VIBRANT LOW-CONTRAST APPLE THEMES ---
LIGHT_THEME = {
    'bg': '#F5F7FA', 'card': '#FFFFFF', 'text': "#000000", 'subtext': "#000000",
    'accent': '#007AFF', 'accent_hover': '#3395FF', 'accent_pressed': '#0056B3',
    'secondary': '#E2E8F0', 'success': '#34C759', 'warning': '#FF9500', 'danger': '#FF3B30',
    'danger_hover': '#FF6961', 'danger_pressed': '#CC2922', 'border': '#CBD5E0'
}

DARK_THEME = {
    'bg': '#1C1C1E', 'card': '#2C2C2E', 'text': '#E5E5EA', 'subtext': '#E5E5EA',
    'accent': '#0A84FF', 'accent_hover': '#5E5CE6', 'accent_pressed': '#0040DD',
    'secondary': '#3A3A3C', 'success': "#129127", 'warning': '#FF9F0A', 'danger': '#FF453A',
    'danger_hover': '#FF6961', 'danger_pressed': '#D70015', 'border': '#48484A'
}

class ProductionCalibrationUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Calibration")
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.resize(1150, 800) # Lets Windows handle the screen limits
        
        self.is_dark_mode = False
        self.theme = LIGHT_THEME
        
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

        self.grid_history = []
        self.init_ui()
        self.apply_theme() # Boots up the CSS and colors

        self.net_timer = QTimer(self)
        self.net_timer.setInterval(10)
        self.net_timer.timeout.connect(self.poll_network)
        self.net_timer.start()

        self.gui_timer = QTimer(self)
        self.gui_timer.setInterval(33) 
        self.gui_timer.timeout.connect(self.update_gui)
        self.gui_timer.start()
        self.last_ping = 0

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self.theme = DARK_THEME if self.is_dark_mode else LIGHT_THEME
        self.btn_theme.setText("☀️ Light Mode" if self.is_dark_mode else "🌙 Dark Mode")
        self.apply_theme()
        self.refresh_badges()

    def save_grid_state(self):
        if not self.calib: return
        state = {
            'l': self.calib.get('gear_col_left', -58.0),
            'r': self.calib.get('gear_col_right', -48.0),
            't': self.calib.get('gear_row_top', -1500),
            'b': self.calib.get('gear_row_bot', 1500)
        }
        # Only save if it's a new layout
        if not self.grid_history or self.grid_history[-1] != state:
            self.grid_history.append(state)
            if len(self.grid_history) > 20: # Cap memory at 20 undo steps
                self.grid_history.pop(0)
        
        if hasattr(self, 'btn_undo_grid'):
            self.btn_undo_grid.setEnabled(len(self.grid_history) > 1)

    def undo_grid_move(self):
        if len(self.grid_history) > 1:
            self.grid_history.pop() # Discard current state
            prev = self.grid_history[-1] # Peek at previous state
            
            # Silently snap lines back to previous coordinates
            self.is_syncing_ui = True 
            self.line_col_left.setValue(prev['l'])
            self.line_col_right.setValue(prev['r'])
            self.line_row_top.setValue(prev['t'])
            self.line_row_bot.setValue(prev['b'])
            self.is_syncing_ui = False
            
            # Fire the network update to the board
            self.on_gate_line_dragged() 
            self.btn_undo_grid.setEnabled(len(self.grid_history) > 1)

    def apply_theme(self):
        t = self.theme
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {t['bg']}; }}
            QWidget {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px; color: {t['text']}; }}
            QLabel#subText {{ color: {t['subtext']}; }}
            QGroupBox {{ background-color: {t['card']}; border-radius: 12px; margin-top: 24px; font-weight: bold; font-size: 14px; color: {t['accent']}; border: 1px solid {t['border']}; }}
            QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; padding: 0 10px; top: 4px; left: 10px; background-color: {t['bg']}; }}
            QTabWidget::pane {{ border: none; background: transparent; }}
            QTabBar::tab {{ background: {t['secondary']}; color: {t['subtext']}; border-radius: 8px; padding: 8px 20px; margin: 4px; font-weight: bold; }}
            QTabBar::tab:selected {{ background: {t['accent']}; color: #FFFFFF; }}
            QPushButton {{ background-color: {t['accent']}; color: #FFFFFF; border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {t['accent_hover']}; }}
            QPushButton:pressed {{ background-color: {t['accent_pressed']}; }}
            QPushButton#dangerBtn {{ background-color: {t['danger']}; color: #FFFFFF; }}
            QPushButton#dangerBtn:hover {{ background-color: {t['danger_hover']}; }}
            QPushButton#dangerBtn:pressed {{ background-color: {t['danger_pressed']}; }}
            QPushButton#themeBtn {{ background-color: {t['secondary']}; color: {t['text']}; }}
            QPushButton#themeBtn:hover {{ background-color: {t['border']}; }}
            
            /* Perfectly rounded progress bar chunks */
            QProgressBar {{ background-color: {t['secondary']}; border-radius: 6px; text-align: center; color: transparent; max-height: 12px; }}
            QProgressBar::chunk {{ background-color: {t['success']}; border-radius: 6px; }}
            
            QSlider::groove:horizontal {{ border-radius: 4px; height: 8px; background: {t['secondary']}; }}
            QSlider::handle:horizontal {{ background: #FFFFFF; border: 2px solid {t['accent']}; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }}
            QComboBox {{ border: 1px solid {t['border']}; border-radius: 6px; padding: 4px 8px; background: {t['card']}; color: {t['text']}; }}
            QComboBox::drop-down {{ border: none; }}
            QCheckBox {{ color: {t['text']}; spacing: 8px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {t['border']}; background: {t['card']}; }}
            QCheckBox::indicator:checked {{ background: {t['accent']}; border: 1px solid {t['accent']}; }}
        """)
        
        # Update pyqtgraph 2D Mapping
        self.plot_widget.setBackground(t['card'])
        self.plot_widget.getAxis('bottom').setPen(t['text'])
        self.plot_widget.getAxis('left').setPen(t['text'])
        
        # Keep Active Gear label colored to the accent
        self.lbl_active_gear.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {t['accent']};")

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        header_layout = QHBoxLayout()
        self.status_bar = QLabel(" OFFLINE")
        self.sync_badge = QLabel(" WAITING FOR BOARD...")
        
        header_layout.addWidget(self.status_bar)
        header_layout.addWidget(self.sync_badge)
        header_layout.addStretch()

        self.btn_theme = QPushButton("🌙 Dark Mode")
        self.btn_theme.setObjectName("themeBtn")
        self.btn_theme.clicked.connect(self.toggle_theme)
        
        btn_import = QPushButton("Import JSON")
        btn_import.clicked.connect(self.import_profile)
        btn_export = QPushButton("Export JSON")
        btn_export.clicked.connect(self.export_profile)
        
        header_layout.addWidget(self.btn_theme)
        header_layout.addWidget(btn_import)
        header_layout.addWidget(btn_export)
        
        main_layout.addLayout(header_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_pedals_tab(), "Pedals")
        self.tabs.addTab(self.create_gearbox_tab(), "Gearbox")
        self.tabs.addTab(self.create_tillers_tab(), "Tillers")
        main_layout.addWidget(self.tabs)

        cmd_layout = QHBoxLayout()
        self.save_btn = QPushButton("FLASH WRITE (PERSIST)")
        self.save_btn.setMinimumHeight(50)
        self.save_btn.clicked.connect(self.save_to_flash)

        self.reset_btn = QPushButton("RESET DEFAULTS")
        self.reset_btn.setObjectName("dangerBtn") 
        self.reset_btn.setMinimumHeight(50)
        self.reset_btn.clicked.connect(self.reset_defaults)

        cmd_layout.addWidget(self.save_btn, 3)
        cmd_layout.addWidget(self.reset_btn, 1)
        main_layout.addLayout(cmd_layout)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        self.update_controls_state()
        self.refresh_badges()

    # ------------------- TAB CREATION -------------------
    def create_pedals_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        self.pedal_bars = {}
        self.pedal_raw_labels = {}

        for pedal, name in [('accel', 'Throttle'), ('clutch', 'Clutch'), ('brake', 'Brake Hybrid')]:
            box = QGroupBox(f"  {name}")
            vbox = QVBoxLayout()
            vbox.setContentsMargins(20, 30, 20, 20)
            
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            vbox.addWidget(pbar)
            
            raw_lbl = QLabel("Raw: --")
            raw_lbl.setObjectName("subText")
            vbox.addWidget(raw_lbl)

            btn_hbox = QHBoxLayout()
            btn_min = QPushButton("Set Min")
            btn_max = QPushButton("Set Max")
            btn_min.clicked.connect(lambda _, p=pedal: self.capture_pedal_limit(p, 'min'))
            btn_max.clicked.connect(lambda _, p=pedal: self.capture_pedal_limit(p, 'max'))
            btn_hbox.addWidget(btn_min)
            btn_hbox.addWidget(btn_max)
            
            vbox.addLayout(btn_hbox)
            box.setLayout(vbox)
            layout.addWidget(box)
            self.pedal_bars[pedal] = pbar
            self.pedal_raw_labels[pedal] = raw_lbl

        blend_box = QGroupBox("  Brake Trust Blender")
        bv = QVBoxLayout()
        bv.setContentsMargins(20, 30, 20, 20)
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
        layout.setContentsMargins(10, 10, 10, 10)
        controls_layout = QVBoxLayout()
        
        telemetry_box = QGroupBox("  Telemetry & Hardware")
        tb_layout = QVBoxLayout()
        tb_layout.setContentsMargins(20, 30, 20, 20)
        
        self.lbl_active_gear = QLabel("NEUTRAL")
        
        self.lbl_gangle = QLabel("X-Axis (Angle): --")
        self.lbl_hfused = QLabel("Y-Axis (H_Fused): --")
        self.lbl_h1_raw = QLabel("H1 Raw (ADC): --")
        self.lbl_h1_raw.setObjectName("subText")
        self.lbl_h2_raw = QLabel("H2 Raw (ADC): --")
        self.lbl_h2_raw.setObjectName("subText")
        
        tb_layout.addWidget(QLabel("ACTIVE GEAR:"))
        tb_layout.addWidget(self.lbl_active_gear)
        tb_layout.addSpacing(10)
        tb_layout.addWidget(self.lbl_gangle)
        tb_layout.addWidget(self.lbl_hfused)
        tb_layout.addWidget(self.lbl_h1_raw)
        tb_layout.addWidget(self.lbl_h2_raw)
        tb_layout.addSpacing(20)

        self.sensor_mode_combo = QComboBox()
        self.sensor_mode_combo.addItems(["Differential (H1 & H2)", "Primary (H1 Only)", "Secondary (H2 Only)"])
        self.sensor_mode_combo.currentIndexChanged.connect(self.on_h_sensor_mode_changed)
        tb_layout.addWidget(QLabel("<b>Y-Axis Sensor Mode:</b>"))
        tb_layout.addWidget(self.sensor_mode_combo)
        
        telemetry_box.setLayout(tb_layout)
        controls_layout.addWidget(telemetry_box)
        controls_layout.addStretch()

        graph_box = QGroupBox("  2D Gate Mapping")
        gb_layout = QVBoxLayout()
        gb_layout.setContentsMargins(20, 30, 20, 20)

        grid_top_layout = QHBoxLayout()
        self.btn_undo_grid = QPushButton("⎌ Undo Move")
        self.btn_undo_grid.setObjectName("themeBtn") # Uses the secondary soft styling
        self.btn_undo_grid.setEnabled(False)
        self.btn_undo_grid.clicked.connect(self.undo_grid_move)
        grid_top_layout.addStretch()
        grid_top_layout.addWidget(self.btn_undo_grid)
        gb_layout.addLayout(grid_top_layout)
        
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.plotItem.invertX(True)
        self.plot_widget.plotItem.invertY(True)
        
        self.stick_marker = self.plot_widget.plot([0], [0], pen=None, symbol='o', symbolSize=22, symbolBrush=QColor(LIGHT_THEME['success']))

        lbl_opts = {'position': 0.85, 'color': (255, 255, 255), 'fill': (0, 122, 255, 200), 'movable': True}
        hover_pen = pg.mkPen("#E0E4E9", width=5)

        self.line_col_left = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen('#007AFF', width=3), hoverPen=hover_pen)
        # self.line_col_left = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen('#007AFF', width=3), hoverPen=hover_pen, label='L', labelOpts=lbl_opts)
        self.line_col_right = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen('#AF52DE', width=3, style=Qt.PenStyle.DashLine), hoverPen=hover_pen)
        # self.line_col_right = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen('#AF52DE', width=3, style=Qt.PenStyle.DashLine), hoverPen=hover_pen, label='R', labelOpts=lbl_opts)
        self.line_row_top = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen('#FF9500', width=3), hoverPen=hover_pen)
        self.line_row_bot = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen('#FF2D55', width=3, style=Qt.PenStyle.DashLine), hoverPen=hover_pen)

        for line in [self.line_col_left, self.line_col_right, self.line_row_top, self.line_row_bot]:
            self.plot_widget.addItem(line)
            line.sigPositionChanged.connect(self.on_gate_line_dragged)
            line.sigPositionChangeFinished.connect(self.save_grid_state)

        gb_layout.addWidget(self.plot_widget)
        graph_box.setLayout(gb_layout)

        layout.addLayout(controls_layout, 1)
        layout.addWidget(graph_box, 3)
        tab.setLayout(layout)
        return tab

    def create_tillers_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        self.tiller_bars = {}
        self.tiller_raw_lbls = {}
        self.tiller_inverts = {}

        for stick, name, prefix in [('l_stick', 'Left Tiller', 'ls'), ('r_stick', 'Right Tiller', 'rs')]:
            box = QGroupBox(f"  {name}")
            vbox = QVBoxLayout()
            vbox.setContentsMargins(20, 30, 20, 20)
            
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            vbox.addWidget(pbar)
            
            lbl_raw = QLabel("Raw: 0")
            lbl_raw.setObjectName("subText")
            vbox.addWidget(lbl_raw)

            hz_controls = QHBoxLayout()
            inv_cb = QCheckBox("Invert Axis Direction")
            inv_cb.stateChanged.connect(lambda state, p=prefix: self.on_tiller_invert(p, state))
            self.tiller_inverts[stick] = inv_cb
            
            hz_controls.addWidget(inv_cb)
            hz_controls.addStretch()
            vbox.addLayout(hz_controls)
            vbox.addSpacing(10)

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
    def refresh_badges(self):
        """Forces the status badges to instantly recolor when themes change"""
        t = self.theme
        if self.synced_with_board:
            self.status_bar.setStyleSheet(f"background-color: {t['success']}; color: #FFFFFF; border-radius: 8px; padding: 6px 12px; font-weight: bold;")
        else:
            self.status_bar.setStyleSheet(f"background-color: {t['danger']}; color: #FFFFFF; border-radius: 8px; padding: 6px 12px; font-weight: bold;")
            
        if self.unsaved_changes:
            self.sync_badge.setStyleSheet(f"background-color: {t['warning']}; color: #FFFFFF; border-radius: 8px; padding: 6px 12px; font-weight: bold;")
        elif self.synced_with_board:
            self.sync_badge.setStyleSheet(f"background-color: {t['success']}; color: #FFFFFF; border-radius: 8px; padding: 6px 12px; font-weight: bold;")
        else:
            self.sync_badge.setStyleSheet(f"background-color: {t['subtext']}; color: #FFFFFF; border-radius: 8px; padding: 6px 12px; font-weight: bold;")

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

        if latest_data is not None and len(latest_data) >= 176:
            if not self.synced_with_board:
                self.synced_with_board = True
                self.status_bar.setText(" ONLINE (100Hz)")
                self.refresh_badges()
            
            self.last_packet_time = now

            try:
                t_raw = struct.unpack('<5f 4i 3f 1i 2I 1i 1f 1i 2f 3i', latest_data[:92])
                t_up = [0.0 if (isinstance(x, float) and math.isnan(x)) else x for x in t_raw]
                
                self.latest_pkt = {
                    'accel': t_up[2], 'brake': t_up[3], 'clutch': t_up[4],
                    'r_stick': t_up[0], 'l_stick': t_up[1], 'r_raw': t_up[6], 'l_raw': t_up[7],
                    'th_angle_raw': t_up[9], 'cl_angle_raw': t_up[10], 'br_angle_raw': t_up[11], 'br_lc_raw': t_up[12],
                    'h1_adc': t_up[13], 'h2_adc': t_up[14], 'h_fused': t_up[15], 'gear_angle_raw': t_up[16], 'raw_gear': t_up[17]
                }
                
                if self.is_syncing_ui == False and self.calib == {}:
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
                    self.set_unsaved_changes(False)
                    self.update_controls_state()

            except Exception as e:
                print(f"Unpack error: {e}")

        elif now - self.last_packet_time > 2.0 and self.synced_with_board:
            self.synced_with_board = False
            self.status_bar.setText(" OFFLINE")
            self.refresh_badges()
            self.update_controls_state()

    def sync_ui_to_dict(self):
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

        for stick, prefix in [('l_stick', 'ls'), ('r_stick', 'rs')]:
            self.tiller_inverts[stick].blockSignals(True)
            self.tiller_inverts[stick].setChecked(bool(self.calib.get(f"{prefix}_invert", 0)))
            self.tiller_inverts[stick].blockSignals(False)

        if self.latest_pkt:
            c_ang = self.latest_pkt['gear_angle_raw']
            c_fus = self.latest_pkt['h_fused']
            self.plot_widget.setXRange(c_ang - 40, c_ang + 40)
            y_span = 1000 if self.calib.get('h_sensor_mode', 0) > 0 else 2500
            self.plot_widget.setYRange(c_fus - y_span, c_fus + y_span)

        self.is_syncing_ui = False
        self.save_grid_state()

    def update_gui(self):
        if not self.latest_pkt or not self.synced_with_board: return
        pkt = self.latest_pkt

        # Keep alpha at 0.6 for snappy, low-lag rendering
        alpha = 0.6 
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
        
        # The restored H1 and H2 Raw values!
        self.lbl_h1_raw.setText(f"H1 Raw (ADC): {pkt['h1_adc']}")
        self.lbl_h2_raw.setText(f"H2 Raw (ADC): {pkt['h2_adc']}")
        
        rg = pkt['raw_gear']
        if rg == 6: gear_str = "REVERSE"
        elif rg > 0: gear_str = f"GEAR {rg}"
        else: gear_str = "NEUTRAL"
        self.lbl_active_gear.setText(gear_str)
        
        self.stick_marker.setData([self.smoothed_angle], [self.smoothed_hfused])
        self.stick_marker.setSymbolBrush(QColor(self.theme['success']))
        
        self.tiller_bars['l_stick'].setValue(max(0, min(100, int(pkt['l_stick'] * 100))))
        self.tiller_bars['r_stick'].setValue(max(0, min(100, int(pkt['r_stick'] * 100))))
        self.tiller_raw_lbls['l_stick'].setText(f"Raw: {pkt['l_raw']}")
        self.tiller_raw_lbls['r_stick'].setText(f"Raw: {pkt['r_raw']}")

    # ------------------- ACTIONS -------------------
    def update_controls_state(self):
        enabled = self.synced_with_board
        self.tabs.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)

    def set_unsaved_changes(self, changed: bool):
        self.unsaved_changes = changed
        if changed:
            self.sync_badge.setText(" UNSAVED CHANGES (IN RAM)")
        else:
            self.sync_badge.setText(" SAVED TO FLASH")
        self.refresh_badges()

    def export_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Profile", "", "JSON Files (*.json)")
        if path:
            with open(path, 'w') as f:
                json.dump(self.calib, f, indent=4)

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Profile", "", "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'r') as f:
                    self.calib.update(json.load(f))
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
            self.calib[f"{'th' if pedal == 'accel' else 'cl'}_{limit_type}"] = val
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
        # Reset (0x03) is allowed while unsynced; everything else needs a synced UI
        if not self.synced_with_board and cmd_type != 0x03:
            return

        c = self.calib

        def f(key, default=0.0):
            """Float field; replaces missing, NaN or infinite values with the default."""
            try:
                v = float(c.get(key, default))
            except (TypeError, ValueError):
                return float(default)
            return v if math.isfinite(v) else float(default)

        def i(key, default=0):
            """Int field; same protection."""
            return int(round(f(key, default)))

        try:
            col_lo = min(f('gear_col_left', -58.0), f('gear_col_right', -48.0))
            col_hi = max(f('gear_col_left', -58.0), f('gear_col_right', -48.0))
            row_lo = min(i('gear_row_top', -1500), i('gear_row_bot', 1500))
            row_hi = max(i('gear_row_top', -1500), i('gear_row_bot', 1500))

            mode = i('h_sensor_mode', 0)
            if mode not in (0, 1, 2):
                mode = 0

            trust = min(1.0, max(0.0, f('br_lc_trust', 0.5)))

            packed = struct.pack(
                CMD_FMT,
                cmd_type, CALIB_MAGIC,
                f('th_min'),       f('th_max'),
                f('cl_min'),       f('cl_max'),
                f('br_hall_min'),  f('br_hall_max'),
                i('br_lc_min'),    i('br_lc_max'),
                trust,
                i('rs_min'),       i('rs_max'),
                i('ls_min'),       i('ls_max'),
                col_lo,            col_hi,
                row_lo,            row_hi,
                mode,
                1 if i('ls_invert') else 0,
                1 if i('rs_invert') else 0,
            )
            self.sock.sendto(packed, (BOARD_IP, BOARD_PORT))
        except Exception as e:
            print(f"UDP Pack Error: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProductionCalibrationUI()
    window.show()
    sys.exit(app.exec())