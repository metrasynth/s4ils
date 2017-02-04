import json
import os
import traceback
from collections import defaultdict
from time import strftime

import io
from PyQt5.QtCore import pyqtSlot
from PyQt5.uic import loadUiType

from arrow import now
import rv.api as rv
from rv.cmidmap import MidiMessageType
from rv.note import NOTE, NOTECMD
from sails.midi.ccmappings import cc_mappings
from sails.ui import App
from sails.ui.mmck.ccmapper import CCMapper
from sails.ui.mmck.filewatcher import FileWatcher
from sails.ui.mmck.noteplayer import NotePlayer
from sails.ui.mmck.sunvoxprocess import SunvoxProcess
from sails.ui.mmck.udcmanager import UdcManager
from sails.ui.outputcatcher import OutputCatcher
from sf.mmck.kit import Kit

from sails.ui.mainmenubar import MainMenuBar
from sails.ui.mmck.parameters.manager import ParametersManager
from sails.ui.mmck.controllers.manager import ControllersManager, Group

UIC_NAME = 'mainwindow.ui'
UIC_PATH = os.path.join(os.path.dirname(__file__), UIC_NAME)

Ui_MmckMainWindow, MmckMainWindowBase = loadUiType(UIC_PATH)

EMPTY_GROUP = Group()


class MmckMainMenuBar(MainMenuBar):

    def create_menus(self):
        super().create_menus()
        self.edit_menu = self.addMenu('&Edit')
        self.transport_menu = self.addMenu('&Transport')
        self.view_menu = self.addMenu('&View')
        self.insertMenu(self.edit_menu.menuAction(), self.edit_menu)
        self.insertMenu(self.transport_menu.menuAction(), self.transport_menu)
        self.insertMenu(self.view_menu.menuAction(), self.view_menu)


class MmckMainWindow(MmckMainWindowBase, Ui_MmckMainWindow):

    def __init__(self):
        super(MmckMainWindow, self).__init__(None)
        self.clear_midi_mapping()
        self.clear_udc_assignments()
        self.loaded_path = None
        self.kit = Kit()
        self.setupUi(self)
        self.play_started = False

    def setupUi(self, ui):
        super(MmckMainWindow, self).setupUi(ui)
        self.setMenuBar(MmckMainMenuBar())
        self.setup_catcher()
        self.setup_menus()
        self.setup_parameters_manager()
        self.setup_udc_manager()
        self.setup_controllers_manager()
        self.setup_file_watcher()
        self.setup_sunvox_process()
        self.setup_note_player()
        self.setup_cc_mapper()

    def setup_catcher(self):
        self.catcher = OutputCatcher(self.output_textedit)

    def setup_menus(self):
        menubar = self.menuBar()
        self.setup_dockwidget_menus(menubar)
        self.setup_file_menus(menubar)
        self.setup_edit_menus(menubar)
        self.setup_transport_menus(menubar)

    def setup_dockwidget_menus(self, menubar):
        for action in [
            self.action_view_output,
            self.action_view_parameters,
            self.action_view_udc,
        ]:
            menubar.view_menu.addAction(action)

    def setup_file_menus(self, menubar):
        sep = menubar.file_menu.insertSeparator(menubar.file_settings)
        for action in [
            self.action_save,
            self.action_save_as,
            self.action_export_metamodule,
            self.action_export_project,
        ]:
            menubar.file_menu.insertAction(sep, action)

    def setup_edit_menus(self, menubar):
        for action in [
            self.action_copy_to_sunvox_clipboard,
            self.action_paste_from_sunvox_clipboard,
        ]:
            menubar.edit_menu.addAction(action)

    def setup_transport_menus(self, menubar):
        for action in [
            self.action_play_from_beginning,
            self.action_stop,
        ]:
            menubar.transport_menu.addAction(action)

    def setup_parameters_manager(self):
        self.parameters_manager = ParametersManager(
            parent=self.parameters_scrollarea,
            layout=self.parameters_layout,
            parameters={},
            values=self.kit.parameter_values,
        )
        self.parameters_manager.valuesChanged.connect(
            self.on_parameters_manager_valuesChanged)

    def setup_udc_manager(self):
        self.udc_manager = UdcManager(
            parent=self.udc_scrollarea,
            layout=self.udc_layout,
            assignments=self.udc_assignments,
        )

    def setup_controllers_manager(self):
        self.controllers_manager = ControllersManager(
            parent=self.controllers_scrollarea,
            layout=self.controllers_layout,
            root_group=EMPTY_GROUP,
        )
        self.controllers_manager.mappingChanged.connect(
            self.on_controllers_manager_mappingChanged)
        self.controllers_manager.valueChanged.connect(
            self.on_controllers_manager_valueChanged)
        self.controllers_manager.udcChanged.connect(
            self.on_controllers_manager_udcChanged)

    def setup_file_watcher(self):
        self.file_watcher = FileWatcher(self)
        self.file_watcher.fileChanged.connect(self.on_file_watcher_fileChanged)

    def setup_sunvox_process(self):
        self.sunvox_process = SunvoxProcess(self)

    def setup_note_player(self):
        self.note_player = NotePlayer(self)
        self.note_player.noteOn.connect(self.on_note_player_noteOn)
        self.note_player.noteOff.connect(self.on_note_player_noteOff)

    def setup_cc_mapper(self):
        self.cc_mapper = CCMapper(self)
        self.cc_mapper.controlValueChanged.connect(self.on_cc_mapper_controlValueChanged)

    @property
    def slot(self):
        return self.sunvox_process.slot

    def clear_midi_mapping(self):
        self.controller_aliases = defaultdict(set)
        self.alias_controllers = defaultdict(set)

    def clear_udc_assignments(self):
        self.udc_assignments = [set() for _ in range(27)]
        if hasattr(self, 'udc_manager'):
            self.udc_manager.assignments = self.udc_assignments

    def prune_udc_assignments(self):
        for names in self.udc_assignments:
            for name in names.copy():
                if name not in self.kit.controllers:
                    names.remove(name)
        self.udc_manager.assignments = self.udc_assignments

    # noinspection PyBroadException
    def load_file(self, path):
        self.loaded_path = path
        self.kit.name = os.path.basename(path)[:-len('.mmckpy')]
        self.setWindowTitle(path)
        self.parameters_manager.clear_widgets()
        with self.catcher:
            try:
                with open(path, 'r') as f:
                    self.kit.py_source = f.read()
                self.load_kit_parameter_values()
                self.parameters_manager.parameters = self.kit.parameters
                self.rebuild_project()
                self.auto_map_controllers()
                self.clear_udc_assignments()
            except Exception:
                print(traceback.format_exc())
                return
            else:
                print('Finished loading at {}'.format(strftime('%c')))
        self.file_watcher.paths = [path] + self.kit.watch_paths

    def auto_map_controllers(self):
        for alias, (name, w) in zip(cc_mappings.options[1:], self.controllers_manager.controller_widgets.items()):
            try:
                w.set_cc_alias(alias)
            except (SystemError, RuntimeError):
                continue
            mod = w.module
            ctl = w.ctl
            ccs = cc_mappings.alias_ccs[alias]
            if ccs:
                cc = list(sorted(ccs))[0]
                midmap = mod.controller_midi_maps[ctl.name]
                midmap.message_type = MidiMessageType.control_change
                midmap.message_parameter = cc

    def rebuild_project(self):
        values = self.controllers_manager.save_values()
        del self.kit.project
        self.sunvox_process.slot.load(self.kit.project)
        self.controllers_manager.root_group = self.kit.controllers
        self.controllers_manager.restore_values(values)
        self.prune_udc_assignments()
        self.controllers_manager.restore_udc_assignments(self.udc_assignments)

    def load_kit_parameter_values(self):
        App.settings.beginGroup('mmck_params')
        try:
            saved_params = App.settings.value(self.kit.name)
            if saved_params:
                self.kit.parameter_values.update(saved_params)
        finally:
            App.settings.endGroup()

    def save_kit_parameter_values(self):
        App.settings.beginGroup('mmck_params')
        try:
            App.settings.setValue(self.kit.name, dict(self.kit.parameter_values))
        finally:
            App.settings.endGroup()

    def export_metamodule(self, filename=None):
        # make a clone of the project as we may be modifying it
        with io.BytesIO() as f:
            project = self.kit.project.write_to(f)
            f.seek(0)
            project = rv.read_sunvox_file(f)
        metamod = rv.m.MetaModule(project=project, name=project.name, apply_velocity_to_project=True)
        assignments = self.udc_assignments[:]
        while assignments and not assignments[-1]:
            assignments.pop()
        metamod.user_defined_controllers = len(assignments)
        for i, names in enumerate(assignments):
            mapping = metamod.mappings.values[i]
            if len(names) == 0:
                mapping.module = 0
                mapping.controller = 1
                metamod.user_defined[i].label = ''
            elif len(names) == 1:
                # direct connection
                name = list(names).pop()
                grpname, ctlname = name.split('.')
                controller = self.kit.controllers[grpname][ctlname]
                mapping.module = controller.module.index
                mapping.controller = controller.ctl.number
                metamod.user_defined[i].label = name
            else:
                # bundle into multictl
                c = self.kit.controllers
                macro = rv.m.MultiCtl.macro(
                    project,
                    *[(c[name].module, c[name].ctl) for name in names],
                    name='macro-{}'.format(i + 1),
                    layer=7,
                    x=i * 80,
                    y=-80,
                )
                mapping.module = macro.index
                mapping.controller = 1
                metamod.user_defined[i].label = ','.join(sorted(names))
        mmckdata = project.new_module(rv.m.Sampler, name='mmckdata', layer=7, x=project.output.x, y=project.output.y)
        s1 = mmckdata.samples[-1] = mmckdata.Sample()
        s1.data = self.kit.py_source.encode('utf8')
        s1.format = mmckdata.Format.int8
        s1.channels = mmckdata.Channels.mono
        s2 = mmckdata.samples[-2] = mmckdata.Sample()
        s2.data = json.dumps(self.kit.parameter_values).encode('utf8')
        s2.format = mmckdata.Format.int8
        s2.channels = mmckdata.Channels.mono
        synth = rv.Synth(metamod)
        if filename is None:
            slug = project.name.lower().replace(' ', '-')
            timestamp = now().strftime('%Y%m%d%H%M%S')
            filename = '{}-{}-{}.sunsynth'.format(self.loaded_path, slug, timestamp)
        with open(filename, 'wb') as f:
            synth.write_to(f)
        print('Exported synth to {}'.format(filename))

    @pyqtSlot(str, str)
    def on_controllers_manager_mappingChanged(self, alias, name):
        # first remove existing controller/alias mappings
        for a in list(self.controller_aliases[name]):
            if name in self.alias_controllers[a]:
                self.alias_controllers[a].remove(name)
            if a in self.controller_aliases[name]:
                self.controller_aliases[name].remove(a)
        self.alias_controllers[alias].add(name)
        self.controller_aliases[name].add(alias)

    @pyqtSlot(int, str)
    def on_controllers_manager_valueChanged(self, value, name):
        c = self.controllers_manager.root_group
        if c:
            controller = c[name]
            mod = controller.module
            ctl = controller.ctl
            pvalue = ctl.pattern_value(value)
            setattr(mod, ctl.name, value)
            self.slot.send_event(0, 0, 0, mod.index + 1, ctl.number << 8, pvalue)

    @pyqtSlot(int, str)
    def on_controllers_manager_udcChanged(self, pos, name):
        pos -= 1
        if pos < 0 or pos > 26:
            return
        for names in self.udc_assignments:
            if name in names:
                names.remove(name)
        self.udc_assignments[pos].add(name)
        self.udc_manager.assignments = self.udc_assignments

    # noinspection PyBroadException
    @pyqtSlot()
    def on_parameters_manager_valuesChanged(self):
        with self.catcher:
            try:
                self.rebuild_project()
                self.auto_map_controllers()
            except Exception:
                print(traceback.format_exc())
                return
            else:
                print('Rebuilt project at {}'.format(strftime('%c')))
        if self.kit.name:
            self.save_kit_parameter_values()

    @pyqtSlot()
    def on_file_watcher_fileChanged(self):
        if self.loaded_path:
            self.load_file(self.loaded_path)

    @pyqtSlot(str, int)
    def on_cc_mapper_controlValueChanged(self, name, value):
        self.controllers_manager.set_ctl_value(name, value)

    @pyqtSlot(int, int, int)
    def on_note_player_noteOn(self, track, note, velocity):
        module = 2
        self.slot.send_event(track, note + 1, velocity, module, 0, 0)

    @pyqtSlot(int)
    def on_note_player_noteOff(self, track):
        self.slot.send_event(track, NOTECMD.NOTE_OFF, 0, 0, 0, 0)

    @pyqtSlot()
    def on_action_copy_to_sunvox_clipboard_triggered(self):
        with self.catcher.more:
            workspace_paths = App.settings.value('sunvox/workspace_paths')
            if len(workspace_paths) == 0:
                print('To copy to SunVox clipboard, set a workspace path in app settings')
            else:
                path = workspace_paths[0]
                filename = os.path.join(path, '.sunvox_clipboard.sunsynth')
                self.export_metamodule(filename)

    @pyqtSlot()
    def on_action_paste_from_sunvox_clipboard_triggered(self):
        pass

    @pyqtSlot()
    def on_action_export_metamodule_triggered(self, filename=None):
        if self.loaded_path:
            with self.catcher.more:
                self.export_metamodule()

    @pyqtSlot()
    def on_action_export_project_triggered(self):
        path = self.loaded_path
        if path:
            with self.catcher.more:
                project = self.kit.project
                slug = project.name.lower().replace(' ', '-')
                timestamp = now().strftime('%Y%m%d%H%M%S')
                filename = '{}-{}-{}.sunvox'.format(path, slug, timestamp)
                with open(filename, 'wb') as f:
                    project.write_to(f)
                print('Exported project to {}'.format(filename))

    @pyqtSlot()
    def on_action_play_from_beginning_triggered(self):
        with self.catcher.more:
            print('Play from beginning')
            self.slot.play_from_beginning()
            self.play_started = True

    @pyqtSlot()
    def on_action_stop_triggered(self):
        with self.catcher.more:
            self.slot.stop()
            self.slot.send_event(0, NOTECMD.ALL_NOTES_OFF, 0, 0, 0, 0)
            if not self.play_started:
                print('Clean synths')
                self.slot.send_event(0, NOTECMD.CLEAN_SYNTHS, 0, 0, 0, 0)
            else:
                print('Stop')
                self.play_started = False
