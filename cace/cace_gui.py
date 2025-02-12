# Copyright 2024 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
This is a Python tkinter script that handles local
project management.  Much of this involves the
running of ng-spice for characterization, allowing
the user to determine where a circuit is failing
characterization.
"""

import io
import os
import sys
import copy
import json
import time
import signal
import select
import argparse
import datetime

import tkinter
from tkinter import ttk
from tkinter import filedialog

from .gui.style import init_style
from .gui.tksimpledialog import *
from .gui.tooltip import *
from .gui.consoletext import ConsoleText
from .gui.helpwindow import HelpWindow
from .gui.failreport import FailReport
from .gui.textreport import TextReport
from .gui.editparam import EditParam
from .gui.settings import Settings
from .gui.simhints import SimHints
from .gui.rowwidget import RowWidget

from .common.cace_read import *
from .common.cace_compat import *
from .common.cace_write import *
from .common.simulation_manager import SimulationManager

# Application path (path where this script is located)
apps_path = os.path.realpath(os.path.dirname(__file__))


class ConfirmDialog(Dialog):
    """Simple dialog for confirming quit"""

    def body(self, master, warning, seed):
        ttk.Label(master, text=warning, wraplength=500).grid(
            row=0, columnspan=2, sticky='wns'
        )
        return self

    def apply(self):
        return 'okay'


class CACEGui(ttk.Frame):
    """Main class for this application"""

    def __init__(self, parent, *args, **kwargs):
        ttk.Frame.__init__(self, parent, *args, **kwargs)
        self.root = parent
        self.simulation_manager = SimulationManager()
        self.init_gui()
        parent.protocol('WM_DELETE_WINDOW', self.on_quit)

    def on_quit(self):
        """Exits program."""
        if not self.check_saved():
            warning = 'Warning:  Simulation results have not been saved.'
            confirm = ConfirmDialog(self, warning).result
            if not confirm == 'okay':
                print('Quit canceled.')
                return

        # Cancel all queued and running simulations and join
        print('Stopping all simulations for shutdown.')
        self.simulation_manager.clear_queued_parameters(cancel_cb=True)
        self.simulation_manager.cancel_running_parameters(cancel_cb=True)
        self.simulation_manager.join_parameters()

        if self.logfile:
            self.logfile.close()

        self.quit()

    def on_mousewheel(self, event):
        if event.num == 5:
            self.datasheet_viewer.yview_scroll(1, 'units')
        elif event.num == 4:
            self.datasheet_viewer.yview_scroll(-1, 'units')

    def init_gui(self):
        """Builds GUI."""

        # Initialize the global style
        fontsize = init_style()

        # Create the help window
        self.help = HelpWindow(self, fontsize=fontsize)

        # Create the failure report window
        self.failreport = FailReport(self, fontsize=fontsize)

        # LVS results get a text window of results
        self.textreport = TextReport(self, fontsize=fontsize)

        # Create the settings window
        self.settings = Settings(self, fontsize=fontsize)

        # Create the simulation hints window
        self.simhints = SimHints(self, fontsize=fontsize)

        # Create the edit parameter window
        self.editparam = EditParam(self, fontsize=fontsize)

        # Variables used by option menus and other stuff
        self.origin = tkinter.StringVar(self)
        self.cur_project = tkinter.StringVar(self)
        self.filename = '(no selection)'
        self.logfile = None
        self.parameter_widgets = {}

        # Root window title
        self.root.title('CACE')
        self.root.option_add('*tearOff', 'FALSE')
        self.pack(side='top', fill='both', expand='true')

        self.pane = tkinter.PanedWindow(
            self, orient='vertical', sashrelief='groove', sashwidth=6
        )
        self.toppane = ttk.Frame(self.pane)

        self.toppane.title_frame = ttk.Frame(self.toppane)
        self.toppane.title_frame.grid(column=0, row=2, sticky='nswe')
        self.toppane.title_frame.datasheet_label = ttk.Label(
            self.toppane.title_frame,
            text='CACE Datasheet:',
            style='normal.TLabel',
        )
        self.toppane.title_frame.datasheet_label.grid(column=0, row=0, ipadx=5)

        # New datasheet select button
        self.toppane.title_frame.datasheet_select = ttk.Button(
            self.toppane.title_frame,
            text=self.filename,
            style='normal.TButton',
            command=self.choose_datasheet,
        )
        self.toppane.title_frame.datasheet_select.grid(
            column=1, row=0, ipadx=5
        )

        ToolTip(
            self.toppane.title_frame.datasheet_select,
            text='Select new datasheet file',
        )

        # Show path to datasheet
        self.toppane.title_frame.path_label = ttk.Label(
            self.toppane.title_frame, text=self.filename, style='normal.TLabel'
        )
        self.toppane.title_frame.path_label.grid(
            column=2, row=0, ipadx=5, padx=10
        )

        # Spacer in middle moves selection button to right
        self.toppane.title_frame.sep_label = ttk.Label(
            self.toppane.title_frame, text=' ', style='normal.TLabel'
        )
        self.toppane.title_frame.sep_label.grid(
            column=3, row=0, ipadx=5, padx=10
        )
        self.toppane.title_frame.columnconfigure(3, weight=1)
        self.toppane.title_frame.rowconfigure(0, weight=0)

        # Selection for origin of netlist
        self.toppane.title_frame.origin_label = ttk.Label(
            self.toppane.title_frame,
            text='Netlist from:',
            style='normal.TLabel',
        )
        self.toppane.title_frame.origin_label.grid(
            column=4, row=0, ipadx=5, padx=10
        )

        self.origin.set('Schematic Capture')
        self.toppane.title_frame.origin_select = ttk.OptionMenu(
            self.toppane.title_frame,
            self.origin,
            'Schematic Capture',
            'Schematic Capture',
            'Layout Extracted',
            'C Extracted',
            'R-C Extracted',
            style='blue.TMenubutton',
            command=self.swap_results,
        )
        self.toppane.title_frame.origin_select.grid(column=5, row=0, ipadx=5)

        # ---------------------------------------------
        ttk.Separator(self.toppane, orient='horizontal').grid(
            column=0, row=3, sticky='news'
        )
        # ---------------------------------------------

        # Datasheet information goes here when datasheet is loaded.
        self.mframe = ttk.Frame(self.toppane)
        self.mframe.grid(column=0, row=4, sticky='news')

        # Row 4 (mframe) is expandable, the other rows are not.
        self.toppane.rowconfigure(0, weight=0)
        self.toppane.rowconfigure(1, weight=0)
        self.toppane.rowconfigure(2, weight=0)
        self.toppane.rowconfigure(3, weight=0)
        self.toppane.rowconfigure(4, weight=1)
        self.toppane.columnconfigure(0, weight=1)

        # ---------------------------------------------
        # ttk.Separator(self, orient='horizontal').grid(column=0, row=5, sticky='ew')
        # ---------------------------------------------

        # Add button bar at the bottom of the window
        self.bbar = ttk.Frame(self)

        # Progress bar expands with the window, buttons don't
        self.bbar.columnconfigure(7, weight=1)

        # Define the "quit" button and action
        self.bbar.quit_button = ttk.Button(
            self.bbar,
            text='Quit',
            command=self.on_quit,
            style='normal.TButton',
        )
        self.bbar.quit_button.grid(column=0, row=0, padx=5)

        # Define the save button
        self.bbar.save_button = ttk.Button(
            self.bbar,
            text='Save',
            command=self.save_results,
            style='normal.TButton',
        )
        self.bbar.save_button.grid(column=1, row=0, padx=5)

        # Define the save-as button
        self.bbar.saveas_button = ttk.Button(
            self.bbar,
            text='Save As',
            command=self.save_manual,
            style='normal.TButton',
        )
        self.bbar.saveas_button.grid(column=2, row=0, padx=5)

        # Also a load button
        self.bbar.load_button = ttk.Button(
            self.bbar,
            text='Load',
            command=self.load_manual,
            style='normal.TButton',
        )
        self.bbar.load_button.grid(column=3, row=0, padx=5)

        # Define the HTML generate button
        self.bbar.html_button = ttk.Button(
            self.bbar,
            text='HTML',
            command=self.generate_html,
            style='normal.TButton',
        )
        self.bbar.html_button.grid(column=4, row=0, padx=5)

        # Define help button
        self.bbar.help_button = ttk.Button(
            self.bbar,
            text='Help',
            command=self.help.open,
            style='normal.TButton',
        )
        self.bbar.help_button.grid(column=5, row=0, padx=5)

        # Define settings button
        self.bbar.settings_button = ttk.Button(
            self.bbar,
            text='Settings',
            command=self.settings.open,
            style='normal.TButton',
        )
        self.bbar.settings_button.grid(column=6, row=0, padx=5)

        ToolTip(self.bbar.quit_button, text='Exit characterization tool')
        ToolTip(
            self.bbar.save_button, text='Save current characterization state'
        )
        ToolTip(
            self.bbar.saveas_button, text='Save current characterization state'
        )
        ToolTip(self.bbar.html_button, text='Generate HTML output')
        ToolTip(
            self.bbar.load_button, text='Load characterization state from file'
        )
        ToolTip(self.bbar.help_button, text='Start help tool')
        ToolTip(
            self.bbar.settings_button,
            text='Manage characterization tool settings',
        )

        # Inside frame with main electrical parameter display and scrollbar
        # To make the frame scrollable, it must be a frame inside a canvas.
        self.datasheet_viewer = tkinter.Canvas(self.mframe)
        self.datasheet_viewer.grid(row=0, column=0, sticky='nsew')
        self.datasheet_viewer.dframe = ttk.Frame(
            self.datasheet_viewer, style='bg.TFrame'
        )
        # Place the frame in the canvas
        self.datasheet_viewer.create_window(
            (0, 0),
            window=self.datasheet_viewer.dframe,
            anchor='nw',
            tags='self.frame',
        )

        # Make sure the main window resizes, not the scrollbars.
        self.mframe.rowconfigure(0, weight=1)
        self.mframe.columnconfigure(0, weight=1)
        # X scrollbar for datasheet viewer
        main_xscrollbar = ttk.Scrollbar(self.mframe, orient='horizontal')
        main_xscrollbar.grid(row=1, column=0, sticky='nsew')
        # Y scrollbar for datasheet viewer
        main_yscrollbar = ttk.Scrollbar(self.mframe, orient='vertical')
        main_yscrollbar.grid(row=0, column=1, sticky='nsew')
        # Attach console to scrollbars
        self.datasheet_viewer.config(xscrollcommand=main_xscrollbar.set)
        main_xscrollbar.config(command=self.datasheet_viewer.xview)
        self.datasheet_viewer.config(yscrollcommand=main_yscrollbar.set)
        main_yscrollbar.config(command=self.datasheet_viewer.yview)

        # Make sure that scrollwheel pans window
        self.datasheet_viewer.bind_all('<Button-4>', self.on_mousewheel)
        self.datasheet_viewer.bind_all('<Button-5>', self.on_mousewheel)

        # Set up configure callback
        self.datasheet_viewer.dframe.bind('<Configure>', self.frame_configure)

        # Add the panes once the internal geometry is known
        self.pane.add(self.toppane)
        self.pane.paneconfig(self.toppane, stretch='first')

        # Pack the frames, bbar first so that it gets shrinked last
        self.bbar.pack(side='bottom', fill='x', expand='false')
        self.pane.pack(side='top', fill='both', expand='true')

        # Initialize variables

        # Capture time of start to compare against the annotated
        # output file timestamp.
        self.starttime = time.time()

    def capture_output(self):
        """
        Add a text window below the datasheet to capture output.
        Redirect print statements to it.
        """

        self.botpane = ttk.Frame(self.pane)

        self.botpane.console = ttk.Frame(self.botpane)
        self.botpane.console.pack(side='top', fill='both', expand='true')

        # Add console to GUI
        self.text_box = ConsoleText(
            self.botpane.console, wrap='word', height=4
        )
        self.text_box.pack(side='left', fill='both', expand='true')
        console_scrollbar = ttk.Scrollbar(self.botpane.console)
        console_scrollbar.pack(side='right', fill='y')
        # attach console to scrollbar
        self.text_box.config(yscrollcommand=console_scrollbar.set)
        console_scrollbar.config(command=self.text_box.yview)

        self.pane.add(self.botpane)

        # Redirect stdout and stderr to the gui console
        self.stdout = sys.stdout
        self.stderr = sys.stderr
        sys.stdout = ConsoleText.StdoutRedirector(self.text_box)
        sys.stderr = ConsoleText.StderrRedirector(self.text_box)

    def update_param(self, pname, canceled=False):
        """Update parameter with results, used as callback"""

        if canceled:
            print(f'Simulation of {pname} has been canceled.')
        else:
            print(f'Simulation of {pname} has completed.')

        self.parameter_widgets[pname].update_param(
            self.simulation_manager.find_parameter(pname)
        )
        self.parameter_widgets[pname].update_widgets()

        self.update_simulate_all_button(from_callback=True)

    def simulate_param(self, pname, process=True):
        """Simulate a single parameter"""

        self.simulation_manager.set_runtime_options(
            'netlist_source', self.get_netlist_source()
        )
        self.simulation_manager.set_runtime_options(
            'force', self.settings.get_force()
        )
        self.simulation_manager.set_runtime_options(
            'keep', self.settings.get_keep()
        )
        self.simulation_manager.set_runtime_options(
            'sequential', self.settings.get_sequential()
        )
        self.simulation_manager.set_runtime_options(
            'noplot', self.settings.get_noplot()
        )
        self.simulation_manager.set_runtime_options(
            'debug', self.settings.get_debug()
        )
        self.simulation_manager.set_runtime_options(
            'parallel_parameters', self.settings.get_parallel_parameters()
        )

        # From the GUI, simulation is forced, so clear any "skip" status.
        # TO DO:  "gray out" entries marked as "skip" and require entry to
        # be set to "active" before simulating.
        self.simulation_manager.param_set_status(pname, 'active')

        num_sims = self.simulation_manager.queue_parameter(
            pname, cb=self.update_param
        )

        if not num_sims:
            print("Can't simulate parameter")
            return

        # Set the "Simulate" button to say "in progress"
        self.parameter_widgets[pname].simulate_widget.configure(
            text='(in progress)'
        )

        self.update_simulate_all_button()

        if process:
            self.simulation_manager.run_parameters_async()

    def frame_configure(self, event):
        self.update_idletasks()
        self.datasheet_viewer.configure(
            scrollregion=self.datasheet_viewer.bbox('all')
        )

    def logstart(self):
        # Start a logfile (or append to it, if it already exists)
        # Disabled by default, as it can get very large.
        # Can be enabled from Settings.
        if self.settings.get_log() == True:
            dataroot = os.path.splitext(self.filename)[0]
            if not self.logfile:
                self.logfile = open(dataroot + '.log', 'a')

                # Print some initial information to the logfile.
                self.logprint('-------------------------')
                self.logprint(
                    'Starting new log file '
                    + datetime.datetime.now().strftime('%c'),
                    doflush=True,
                )

    def logstop(self):
        if self.logfile:
            self.logprint('-------------------------', doflush=True)
            self.logfile.close()
            self.logfile = []

    def logprint(self, message, doflush=False):
        if self.logfile:
            self.logfile.buffer.write(message.encode('utf-8'))
            self.logfile.buffer.write('\n'.encode('utf-8'))
            if doflush:
                self.logfile.flush()

    def find_datasheet(self, search_dir):
        debug = self.settings.get_debug()
        if self.simulation_manager.find_datasheet(search_dir, debug):
            # Could not find a datasheet
            return 1
        self.update_filename()
        self.adjust_datasheet_viewer_size()
        self.create_datasheet_view()

    def set_datasheet(self, datasheet_path):
        if self.logfile:
            self.logprint('end of log.')
            self.logprint('-------------------------', doflush=True)
            self.logfile.close()
            self.logfile = None

        debug = self.settings.get_debug()

        # Load the new datasheet
        self.simulation_manager.load_datasheet(datasheet_path, debug)
        self.update_filename()
        self.adjust_datasheet_viewer_size()
        self.create_datasheet_view()

    def update_filename(self):

        self.filename = self.simulation_manager.get_runtime_options('filename')

        if not self.filename:
            print('Error: Filename for datasheet not set!')

        self.toppane.title_frame.datasheet_select.configure(
            text=os.path.split(self.filename)[1]
        )
        self.toppane.title_frame.path_label.configure(text=self.filename)

    def adjust_datasheet_viewer_size(self):
        """Fit datasheet viewer width to desktop"""

        # Attempt to set the datasheet viewer width to the interior width
        # but do not set it larger than the available desktop.
        self.update_idletasks()
        widthnow = self.datasheet_viewer.winfo_width()
        width = self.datasheet_viewer.dframe.winfo_width()
        screen_width = self.root.winfo_screenwidth()
        if width > widthnow:
            if width < screen_width - 10:
                self.datasheet_viewer.configure(width=width)
            else:
                self.datasheet_viewer.configure(width=screen_width - 10)
        elif widthnow > screen_width:
            self.datasheet_viewer.configure(width=screen_width - 10)
        elif widthnow > width:
            self.datasheet_viewer.configure(width=width)

        # Likewise for the height, up to 3/5 of the desktop height.
        height = self.datasheet_viewer.dframe.winfo_height()
        heightnow = self.datasheet_viewer.winfo_height()
        screen_height = self.root.winfo_screenheight()
        if height > heightnow:
            if height < screen_height * 0.6:
                self.datasheet_viewer.configure(height=height)
            else:
                self.datasheet_viewer.configure(height=screen_height * 0.6)
        elif heightnow > screen_height:
            self.datasheet_viewer.configure(height=screen_height - 10)
        elif heightnow > height:
            self.datasheet_viewer.configure(height=height)

    def choose_datasheet(self):
        datasheet = filedialog.askopenfilename(
            multiple=False,
            initialdir=os.getcwd(),
            filetypes=(
                ('Text file', '*.txt'),
                ('JSON File', '*.json'),
                ('All Files', '*.*'),
            ),
            title='Find a datasheet.',
        )
        if datasheet != '':
            self.set_datasheet(datasheet)

    def topfilter(self, line):
        # Check output for ubiquitous "Reference value" lines and remove them.
        # This happens before logging both to the file and to the console.
        refrex = re.compile('Reference value')
        rmatch = refrex.match(line)
        if not rmatch:
            return line
        else:
            return None

    def spicefilter(self, line):
        # Check for the alarmist 'tran simulation interrupted' message and remove it.
        # Check for error or warning and print as stderr or stdout accordingly.
        intrex = re.compile('tran simulation interrupted')
        warnrex = re.compile('.*warning', re.IGNORECASE)
        errrex = re.compile('.*error', re.IGNORECASE)

        imatch = intrex.match(line)
        if not imatch:
            ematch = errrex.match(line)
            wmatch = warnrex.match(line)
            if ematch or wmatch:
                print(line, file=sys.stderr)
            else:
                print(line, file=sys.stdout)

    def printwarn(self, output):
        # Check output for warning or error
        if not output:
            return 0

        warnrex = re.compile('.*warning', re.IGNORECASE)
        errrex = re.compile('.*error', re.IGNORECASE)

        errors = 0
        outlines = output.splitlines()
        for line in outlines:
            try:
                wmatch = warnrex.match(line)
            except TypeError:
                line = line.decode('utf-8')
                wmatch = warnrex.match(line)
            ematch = errrex.match(line)
            if ematch:
                errors += 1
            if ematch or wmatch:
                print(line)
        return errors

    def sim_all(self):
        # Make sure no simulation is running
        if (
            self.simulation_manager.num_queued_parameters()
            + self.simulation_manager.num_running_parameters()
            > 0
        ):
            print('Simulation in progress must finish first.')
            return

        # TODO set at startup and only change directly if necessary
        self.simulation_manager.set_runtime_options(
            'netlist_source', self.get_netlist_source()
        )
        self.simulation_manager.set_runtime_options(
            'force', self.settings.get_force()
        )
        self.simulation_manager.set_runtime_options(
            'keep', self.settings.get_keep()
        )
        self.simulation_manager.set_runtime_options(
            'sequential', self.settings.get_sequential()
        )
        self.simulation_manager.set_runtime_options(
            'noplot', self.settings.get_noplot()
        )
        self.simulation_manager.set_runtime_options(
            'debug', self.settings.get_debug()
        )
        self.simulation_manager.set_runtime_options(
            'parallel_parameters', self.settings.get_parallel_parameters()
        )

        # Queue all of the parameters
        for pname in self.simulation_manager.get_all_pnames():
            self.simulate_param(pname, False)

        # Now simulate all parameters
        self.simulation_manager.run_parameters_async()

        self.update_simulate_all_button()

    def stop_sims(self):
        # Check whether simulations are running
        if (
            self.simulation_manager.num_queued_parameters()
            + self.simulation_manager.num_running_parameters()
            == 0
        ):
            print('No simulation running.')
        else:
            # Cancel all queued and running simulations
            self.simulation_manager.clear_queued_parameters()
            self.simulation_manager.cancel_running_parameters()
            # self.simulation_manager.join_parameters() # TODO deadlock because of GUI cb

            if (
                self.simulation_manager.num_queued_parameters()
                + self.simulation_manager.num_running_parameters()
                == 0
            ):
                print('All simulations have stopped.')
            else:
                print('Not all simulations have stopped yet.')

        self.update_simulate_all_button()

    def update_simulate_all_button(self, from_callback=False):
        # Check whether no simulations are running, or
        # if the function call comes from a callback,
        # only one simulation is running
        if (
            self.simulation_manager.num_queued_parameters()
            + self.simulation_manager.num_running_parameters()
            == 0
            or from_callback
            and self.simulation_manager.num_queued_parameters()
            + self.simulation_manager.num_running_parameters()
            == 1
        ):
            self.allsimbutton.configure(
                style='bluetitle.TButton',
                text='Simulate All',
                command=self.sim_all,
            )
        else:
            # Button now stops the simulations
            self.allsimbutton.configure(
                style='redtitle.TButton',
                text='Stop Simulations',
                command=self.stop_sims,
            )

    def edit_param(self, pname):
        param = self.simulation_manager.find_parameter(pname)

        # Edit the conditions under which the parameter is tested.
        if (
            'editable' in param and param['editable'] == True
        ) or self.settings.get_edit() == True:
            self.editparam.populate(param)
            self.editparam.open()
        else:
            print(f'Parameter {pname} is not editable')

    def copy_param(self, pname):
        # Make a copy of the parameter (for editing)
        self.simulation_manager.duplicate_parameter(pname)

        self.create_datasheet_view()

    def delete_param(self, pname):
        # Remove an electrical parameter from the datasheet.  This is only
        # allowed if the parameter has been copied from another and so does
        # not belong to the original set of parameters.
        self.simulation_manager.delete_parameter(pname)

        self.create_datasheet_view()

    def add_hints(self, param, simbutton):
        # Raise hints window and configure appropriately for the parameter.
        # Fill in any existing hints.
        self.simhints.populate(param, simbutton)
        self.simhints.open()

    # Get the value for runtime options['netlist_source']
    def get_netlist_source(self):
        netlist_text = self.origin.get()
        if netlist_text == 'Schematic Capture':
            return 'schematic'
        elif netlist_text == 'Layout Extracted':
            return 'layout'
        elif netlist_text == 'C Extracted':
            return 'pex'
        elif netlist_text == 'R-C Extracted':
            return 'rcx'
        else:
            print('Unhandled netlist source ' + netlist_text)
            print('Reverting to schematic.')
            return 'schematic'

    def clear_results(self, dsheet):
        # TODO do in SimulationManager

        # Remove results from the window by clearing parameter results
        paramstodo = []
        if 'electrical_parameters' in dsheet:
            paramstodo.extend(dsheet['electrical_parameters'])
        if 'physical_parameters' in dsheet:
            paramstodo.extend(dsheet['physical_parameters'])

        for param in paramstodo:
            # Fill frame with electrical parameter information
            if 'max' in param:
                maxrec = param['max']
                if 'value' in maxrec:
                    maxrec.pop('value')
                if 'score' in maxrec:
                    maxrec.pop('score')
            if 'typ' in param:
                typrec = param['typ']
                if 'value' in typrec:
                    typrec.pop('value')
                if 'score' in typrec:
                    typrec.pop('score')
            if 'min' in param:
                minrec = param['min']
                if 'value' in minrec:
                    minrec.pop('value')
                if 'score' in minrec:
                    minrec.pop('score')
            if 'results' in param:
                param.pop('results')

            if 'plot' in param:
                plotrec = param['plot']
                if 'status' in plotrec:
                    plotrec.pop('status')

        # Regenerate datasheet view
        self.create_datasheet_view()

    def annotate(self, suffix, checktime):
        # Pull results back from datasheet_anno.json.  Do NOT load this
        # file if it predates the unannotated datasheet (that indicates
        # simulator failure, and no results).
        dspath = os.path.split(self.filename)[0]
        if dspath == '':
            dspath = '.'
        dsdir = dspath + '/ngspice'
        anno = dsdir + '/datasheet_' + suffix + '.json'
        unanno = dsdir + '/datasheet.json'

        if os.path.exists(anno):
            statbuf = os.stat(anno)
            mtimea = statbuf.st_mtime
            if checktime >= mtimea:
                # print('original = ' + str(checktime) + ' annotated = ' + str(mtimea))
                print(
                    'Error in simulation, no update to results.',
                    file=sys.stderr,
                )
            elif statbuf.st_size == 0:
                print('Error in simulation, no results.', file=sys.stderr)
            elif os.path.splitext(anno)[1] == '.json':
                with open(anno, 'r') as file:
                    self.simulation_manager.set_datasheet(json.load(file))
            else:
                debug = self.settings.get_debug()
                self.simulation_manager.set_datasheet(cace_read(file, debug))
        else:
            print(
                'Error in simulation, no update to results.', file=sys.stderr
            )

        # Regenerate datasheet view
        self.create_datasheet_view()

        # Close log file, if it was enabled in the settings
        self.logstop()

    def save_results(self):
        # Write datasheet_save with all the locally processed results.
        dspath = os.path.split(self.filename)[0]

        # Save to simulation directory (may want to change this)
        dsheet = self.simulation_manager.get_datasheet()
        paths = dsheet['paths']
        dsdir = os.path.join(dspath, paths['root'], paths['simulation'])

        dfile = os.path.split(self.filename)[1]
        dfileroot = os.path.splitext(dfile)[0]
        dfileext = os.path.splitext(dfile)[1]

        # Output filename is the input datasheet filename + "_save",
        # and the same file extension.
        doutname = dfileroot + '_save' + dfileext
        doutfile = os.path.join(dsdir, doutname)

        if dfileext == '.json':
            with open(doutfile, 'w') as ofile:
                json.dump(
                    dsheet, ofile, indent=4
                )   # TODO inside simulation_manager
        else:
            # NOTE:  This file contains the run-time settings dictionary
            cace_write(dsheet, doutfile)   # TODO inside simulation_manager

        self.last_save = os.path.getmtime(doutfile)

        print('Characterization results saved.')

    def check_saved(self):
        # Check if there is a file 'datasheet_save' and if it is more
        # recent than 'datasheet_anno'.  If so, return True, else False.

        [dspath, dsname] = os.path.split(self.filename)
        dsdir = dspath + '/ngspice'

        savefile = dsdir + '/datasheet_save.json'

        annofile = dsdir + '/datasheet_anno.json'
        if os.path.exists(annofile):
            annotime = os.path.getmtime(annofile)

            # If nothing has been updated since the characterization
            # tool was started, then there is no new information to save.
            if annotime < self.starttime:
                return True

            if os.path.exists(savefile):
                savetime = os.path.getmtime(savefile)
                # return True if (savetime > annotime) else False
                if savetime > annotime:
                    print('Save is more recent than sim, so no need to save.')
                    return True
                else:
                    print('Sim is more recent than save, so need to save.')
                    return False
            else:
                # There is a datasheet_anno file but no datasheet_save,
                # so there are necessarily unsaved results.
                print('no datasheet_save, so any results have not been saved.')
                return False
        else:
            # There is no datasheet_anno file, so datasheet_save
            # is either current or there have been no simulations.
            print('no datasheet_anno, so there are no results to save.')
            return True

    def save_manual(self, value={}):
        # Set initialdir to the project where datasheet is located
        dsparent = os.path.split(self.filename)[0]

        datasheet_path = filedialog.asksaveasfilename(
            initialdir=dsparent,
            confirmoverwrite=True,
            defaultextension='.txt',
            filetypes=(
                ('Text file', '*.txt'),
                ('JSON File', '*.json'),
                ('All Files', '*.*'),
            ),
            title='Select filename for saved datasheet',
        )

        # Save the datasheet
        self.simulation_manager.save_datasheet(datasheet_path)

    def load_manual(self, value={}):
        dspath = self.filename
        # Set initialdir to the project where datasheet is located
        dsparent = os.path.split(dspath)[0]

        datasheet_path = filedialog.askopenfilename(
            multiple=False,
            initialdir=dsparent,
            filetypes=(
                ('Text file', '*.txt'),
                ('JSON File', '*.json'),
                ('All Files', '*.*'),
            ),
            title='Find a datasheet',
        )
        if datasheet_path:
            print('Reading file ' + datasheet_path)

            self.simulation_manager.load_datasheet(datasheet_path, debug)

            # self.set_working_directory()

            self.create_datasheet_view()

    def generate_html(self):
        self.simulation_manager.generate_html()

    def swap_results(self, value={}):
        # This routine just calls self.create_datasheet_view(), but the
        # button callback has an argument that needs to be handled even
        # if it is just discarded.
        self.create_datasheet_view()

    def load_results(self, value={}):

        # Check if datasheet_save exists and is more recent than the
        # latest design netlist.  If so, load it;  otherwise, not.
        # NOTE:  Name of .spice file comes from the project 'name'
        # in the datasheet.

        [dspath, dsname] = os.path.split(self.filename)
        try:
            dsheet = self.simulation_manager.get_datasheet()   # TODO ?
        except KeyError:
            return

        if dspath == '':
            dspath = '.'

        dsroot = dsheet['name']

        # Remove any existing results from the datasheet records
        self.clear_results(dsheet)

        # Also must be more recent than datasheet
        jtime = os.path.getmtime(self.filename)

        # dsroot = os.path.splitext(dsname)[0]

        paths = dsheet['paths']
        dsdir = os.path.join(dspath, paths['root'], paths['simulation'])

        if not os.path.exists(dsdir):
            # Try 'spice' as a subdirectory of the datasheet directory as a
            # fallback.
            dsdir = dspath + '/spice'
            if not os.path.exists(dsdir):
                print('Error:  Cannot find directory spice/ in path ' + dspath)

        if self.origin.get() == 'Layout Extracted':
            spifile = dsdir + '/layout/' + dsroot + '.spice'
        if self.origin.get() == 'C Extracted':
            spifile = dsdir + '/pex/' + dsroot + '.spice'
        elif self.origin.get() == 'R-C Extracted':
            spifile = dsdir + '/rcx/' + dsroot + '.spice'
        else:
            spifile = dsdir + '/' + dsroot + '.spice'

        dsdir = dspath + '/ngspice'
        savefile = dsdir + '/datasheet_save.json'

        if os.path.exists(savefile):
            savetime = os.path.getmtime(savefile)

        if os.path.exists(spifile):
            spitime = os.path.getmtime(spifile)

            if os.path.exists(savefile):
                if savetime > spitime and savetime > jtime:
                    self.annotate('save', 0)
                    print('Characterization results loaded.')
                    # print('(' + savefile + ' timestamp = ' + str(savetime) + '; ' + self.datasheet + ' timestamp = ' + str(jtime))
                else:
                    print('Saved datasheet is out-of-date, not loading')
            else:
                print('Datasheet file ' + savefile)
                print('No saved datasheet file, nothing to pre-load')
        else:
            print('No netlist file ' + spifile + '!')

        # Remove outdated datasheet.json and datasheet_anno.json to prevent
        # them from overwriting characterization document entries

        if os.path.exists(savefile):
            if savetime < jtime:
                print('Removing outdated save file ' + savefile)
                os.remove(savefile)

        savefile = dsdir + '/datasheet_anno.json'
        if os.path.exists(savefile):
            savetime = os.path.getmtime(savefile)
            if savetime < jtime:
                print('Removing outdated results file ' + savefile)
                os.remove(savefile)

        savefile = dsdir + '/datasheet.json'
        if os.path.exists(savefile):
            savetime = os.path.getmtime(savefile)
            if savetime < jtime:
                print('Removing outdated results file ' + savefile)
                os.remove(savefile)

    def create_datasheet_view(self):
        """Create the datasheet view from scratch"""

        dframe = self.datasheet_viewer.dframe

        # Destroy the existing datasheet frame contents (if any)
        for widget in dframe.winfo_children():
            widget.destroy()

        self.parameter_widgets = {}

        dsheet = self.simulation_manager.get_datasheet()

        # Update netlist source
        self.simulation_manager.set_runtime_options(
            'netlist_source', self.get_netlist_source()
        )

        # Add basic information at the top

        n = 0
        dframe.cframe = ttk.Frame(dframe)
        dframe.cframe.grid(column=0, row=n, sticky='ewns', columnspan=10)

        dframe.cframe.plabel = ttk.Label(
            dframe.cframe, text='Project IP name:', style='italic.TLabel'
        )
        dframe.cframe.plabel.grid(column=0, row=n, sticky='ewns', ipadx=5)
        dframe.cframe.pname = ttk.Label(
            dframe.cframe, text=dsheet['name'], style='normal.TLabel'
        )
        dframe.cframe.pname.grid(column=1, row=n, sticky='ewns', ipadx=5)
        if 'foundry' in dsheet:
            dframe.cframe.fname = ttk.Label(
                dframe.cframe, text=dsheet['foundry'], style='normal.TLabel'
            )
            dframe.cframe.fname.grid(column=2, row=n, sticky='ewns', ipadx=5)
        if 'PDK' in dsheet:
            dframe.cframe.fname = ttk.Label(
                dframe.cframe, text=dsheet['PDK'], style='normal.TLabel'
            )
            dframe.cframe.fname.grid(column=3, row=n, sticky='ewns', ipadx=5)
        if 'description' in dsheet:
            dframe.cframe.pdesc = ttk.Label(
                dframe.cframe,
                text=dsheet['description'],
                style='normal.TLabel',
            )
            dframe.cframe.pdesc.grid(column=4, row=n, sticky='ewns', ipadx=5)

        n = 1
        ttk.Separator(dframe, orient='horizontal').grid(
            column=0, row=n, sticky='ewns', columnspan=10
        )

        # Title block
        n += 1
        dframe.desc_title = ttk.Label(
            dframe, text='Parameter', style='title.TLabel'
        )
        dframe.desc_title.grid(column=0, row=n, sticky='ewns')
        dframe.method_title = ttk.Label(
            dframe, text='Testbench', style='title.TLabel'
        )
        dframe.method_title.grid(column=1, row=n, sticky='ewns')
        dframe.min_title = ttk.Label(dframe, text='Min', style='title.TLabel')
        dframe.min_title.grid(column=2, row=n, sticky='ewns', columnspan=2)
        dframe.typ_title = ttk.Label(dframe, text='Typ', style='title.TLabel')
        dframe.typ_title.grid(column=4, row=n, sticky='ewns', columnspan=2)
        dframe.max_title = ttk.Label(dframe, text='Max', style='title.TLabel')
        dframe.max_title.grid(column=6, row=n, sticky='ewns', columnspan=2)
        dframe.stat_title = ttk.Label(
            dframe, text='Status', style='title.TLabel'
        )
        dframe.stat_title.grid(column=8, row=n, sticky='ewns')

        # Check whether simulations are running
        if (
            self.simulation_manager.num_queued_parameters()
            + self.simulation_manager.num_running_parameters()
            > 0
        ):
            self.allsimbutton = ttk.Button(
                dframe,
                text='Stop Simulations',
                style='redtitle.TButton',
                command=self.stop_sims,
            )
        else:
            self.allsimbutton = ttk.Button(
                dframe,
                style='bluetitle.TButton',
                text='Simulate All',
                command=self.sim_all,
            )

        self.allsimbutton.grid(column=9, row=n, sticky='ewns')

        ToolTip(self.allsimbutton, text='Simulate all electrical parameters')

        # Make all columns equally expandable
        for i in range(10):
            dframe.columnconfigure(i, weight=1)

        # Parse the file for electrical parameters
        n += 1
        paramstodo = []
        if 'electrical_parameters' in dsheet:
            paramstodo.extend(dsheet['electrical_parameters'])
        if 'physical_parameters' in dsheet:
            paramstodo.extend(dsheet['physical_parameters'])

        if self.origin.get() == 'Schematic Capture':
            isschem = True
        else:
            isschem = False

        for param in paramstodo:
            self.add_param_to_list(param, n, isschem)
            n += 1

        for child in dframe.winfo_children():
            child.grid_configure(ipadx=5, ipady=1, padx=2, pady=2)

    def add_param_to_list(self, param, n, isschem):
        """Add a row of widgets to the datasheet viewer"""

        dframe = self.datasheet_viewer.dframe
        pname = param['name']

        # Create widgets
        self.parameter_widgets[pname] = RowWidget(
            param,
            dframe,
            self.simulation_manager.get_runtime_options('netlist_source'),
            n,
            self.simulation_manager,
        )

        # Set functions
        self.parameter_widgets[pname].set_functions(
            self.simulate_param,
            self.simulation_manager.cancel_running_parameter,
            self.edit_param,
            self.copy_param,
            self.delete_param,
            self.failreport.display,
            self.textreport.display,
        )


def gui():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='cace-gui',
        description="""Graphical interface for the Circuit Automatic Characterization Engine,
        an analog and mixed-signal design flow system.""",
        epilog='Online documentation at: https://cace.readthedocs.io/',
    )

    # positional argument, optional
    parser.add_argument(
        'datasheet',
        nargs='?',
        help='text or JSON file with the specification of the circuit',
    )

    # on/off flag, optional
    parser.add_argument(
        '-t',
        '--terminal',
        action='store_true',
        help='generate all output to the terminal, not the window',
    )

    # Parse arguments
    args = parser.parse_args()

    # Create tkinter root
    root = tkinter.Tk(className='CACE')
    app = CACEGui(root)

    if not args.terminal:
        app.capture_output()

    if args.datasheet:
        print('Setting datasheet to ' + args.datasheet)
        app.set_datasheet(args.datasheet)
    else:
        if app.find_datasheet(os.getcwd()):
            sys.exit(0)

    # Start the main loop
    root.mainloop()

    # Clean up
    root.destroy()


if __name__ == '__main__':
    gui()
