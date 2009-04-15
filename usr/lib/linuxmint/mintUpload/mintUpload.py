#!/usr/bin/env python

# mintUpload
#	Clement Lefebvre <root@linuxmint.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; Version 2
# of the License.

import sys

try:
	import pygtk
	pygtk.require("2.0")
except:
	pass

try:
	import gtk
	import gtk.glade
	import urllib
	import ftplib
	import os
	import threading
	import datetime
	import gettext
	import paramiko
	import pexpect
	import commands
	from user import home
	from configobj import ConfigObj
except:
	print "You do not have all the dependencies!"
	sys.exit(1)

gtk.gdk.threads_init()

# i18n
gettext.install("messages", "/usr/lib/linuxmint/mintUpload/locale")

class CustomError(Exception):
	'''All custom defined errors'''

	def __init__(self, detail):
		print self.__class__.__name__ + ': ' + detail

class ConnectionError(CustomError):
	'''Raised when an error has occured with an external connection'''
	pass

class FilesizeError(CustomError):
	'''Raised when the file is too large or too small'''
	pass

def sizeStr(size, acc=1, factor=1000):
	'''Converts integer filesize in bytes to textual repr'''

	thresholds = [_("B"),_("KB"),_("MB"),_("GB")]
	size = float(size)
	for i in reversed(range(1,len(thresholds))):
		if size >= factor**i:
			rounded = round(size/factor**i, acc)
			return str(rounded) + thresholds[i]
	return str(int(size)) + thresholds[0]

class spaceChecker(threading.Thread):
	'''Checks that the filesize is ok'''

	def __init__(self, service, filesize):
		self.service = service
		self.filesize = filesize
		threading.Thread.__init__(self)

	def run(self):
		# Get the maximum allowed self.filesize on the service
		if self.service.has_key("maxsize"):
			if self.filesize > self.service["maxsize"]:
				raise FilesizeError(_("File larger than service's maximum"))

		# Get the available space left on the service
		if self.service.has_key("space"):
			try:
				spaceInfo = urllib.urlopen(self.service["space"]).read()
			except:
				raise ConnectionError(_("Could not get available space"))

			spaceInfo = spaceInfo.split("/")
			self.available = int(spaceInfo[0])
			self.total = int(spaceInfo[1])
			if self.filesize > self.available:
				raise FilesizeError(_("File larger than service's available space"))

class mintUploader(threading.Thread):
	'''Uploads the file to the selected service'''

	def run(self):
		global progressbar
		global statusbar
		global selected_service
		global filename
		global wTree
		global url
		global name

		wTree.get_widget("combo").set_sensitive(False)
		wTree.get_widget("upload_button").set_sensitive(False)
		statusbar.push(context_id, _("Connecting to the service..."))
		wTree.get_widget("main_window").window.set_cursor(gtk.gdk.Cursor(gtk.gdk.WATCH))

		wTree.get_widget("frame_progress").show()

		try:
			self.so_far = 0
			self.filesize = os.path.getsize(filename)
			progressbar.set_fraction(0)
			progressbar.set_text("0%")

			# Switch to required connect function, depending on service
			supported_services = {
				'MINT': self._ftp, # For backwards compatiblity
				'FTP' : self._ftp,
				'SFTP': self._sftp,
				'SCP' : self._scp}[selected_service['type']]()

			# Report success
			progressbar.set_fraction(1)
			progressbar.set_text("100%")
			statusbar.push(context_id, "<span color='green'>" + _("File uploaded successfully.") + "</span>")
			label = statusbar.get_children()[0].get_children()[0]
			label.set_use_markup(True)

			#If service is Mint then show the URL
			if selected_service.has_key('url'):
				wTree.get_widget("txt_url").set_text(selected_service['url'])
				wTree.get_widget("txt_url").show()
				wTree.get_widget("lbl_url").show()

		except Exception, detail:
			print detail
			statusbar.push(context_id, "<span color='red'>" + _("Upload failed.") + "</span>")
			label = statusbar.get_children()[0].get_children()[0]
			label.set_use_markup(True)

		finally:
			wTree.get_widget("main_window").window.set_cursor(None)
			wTree.get_widget("combo").set_sensitive(False)
			wTree.get_widget("upload_button").set_sensitive(False)

	def _ftp(self):
		'''Connection process for FTP services'''

		if not selected_service.has_key('port'):
			selected_service['port'] = 21
		try:
			# Attempting to connect
			ftp = ftplib.FTP()
			ftp.connect(selected_service['host'], selected_service['port'])
			ftp.login(selected_service['user'], selected_service['pass'])
			statusbar.push(context_id, selected_service['type'] + _(" connection successfully established"))

			# Create full path
			for dir in selected_service['path'].split(os.sep):
				try:	ftp.mkd(dir)
				except:	pass
				ftp.cwd(dir)

			f = open(filename, "rb")
			statusbar.push(context_id, _("Uploading the file..."))
			ftp.storbinary('STOR ' + name, f, 1024, callback=self.asciicallback)
			f.close()
			ftp.quit()

		except:
			# Close any open connections before raising error
			try:	f.close()
			except:	pass
			try:	ftp.quit()
			except:	pass
			raise

	def getPrivateKey(self):
		'''Find a private key in ~/.ssh'''
		key_files = [home + '/.ssh/id_rsa', home + '/.ssh/id_dsa']
		for f in key_files:
			if os.path.exists(f):
				return paramiko.RSAKey.from_private_key_file(f)

	def _sftp(self):
		'''Connection process for SFTP services'''

		if not selected_service['pass']:
			rsa_key = self.getPrivateKey()
			if not rsa_key:	raise ConnectionError("Connection requires a password or private key!")
		if not selected_service.has_key('port'):
			selected_service['port'] = 22
		try:
			# Attempting to connect
			transport = paramiko.Transport((selected_service['host'], selected_service['port']))
			if selected_service['pass']:
				transport.connect(username = selected_service['user'], password = selected_service['pass'])
			else:
				transport.connect(username = selected_service['user'], pkey = rsa_key)
			statusbar.push(context_id, selected_service['type'] + _(" connection successfully established"))

			# Create full remote path
			path = selected_service['path']
			try:	transport.open_session().exec_command('mkdir -p ' + path)
			except:	pass

			sftp = paramiko.SFTPClient.from_transport(transport)
			statusbar.push(context_id, _("Uploading the file..."))
			sftp.put(filename, path + name)
			sftp.close()
			transport.close()

		except:
			# Close any open connections before raising error
			try:	sftp.close()
			except:	pass
			try:	transport.close()
			except:	pass
			raise

	def _scp(self):
		'''Connection process for SCP services'''

		try:
			# Attempting to connect
			scp_cmd = "scp " + filename + " " + selected_service['user'] + "@" + selected_service['host'] + ':' + selected_service['path']
			scp = pexpect.spawn(scp_cmd)

			if selected_service['pass']:
				scp.expect('.*password:*')
				scp.sendline(selected_service['pass'])

			statusbar.push(context_id, selected_service['type'] + _(" connection successfully established"))

			scp.timeout = None
			received = scp.expect(['.*100\%.*','.*password:.*',pexpect.EOF])
			if received == 1:
				scp.sendline(' ')
				raise ConnectionError("Connection requires a password!")

			scp.close()

		except:
			# Close any open connections before raising error
			try:	scp.close()
			except:	pass
			raise

	def asciicallback(self, buffer):
		global progressbar

		self.so_far = self.so_far+len(buffer)-1
		pct = float(self.so_far)/self.filesize
		progressbar.set_fraction(pct)
		pct = int(pct * 100)
		progressbar.set_text(str(pct) + "%")
		#print "so far:", pct, "%"
		return

class mintUploadWindow:
	"""This is the main class for the application"""

	def __init__(self, filename):
		global wTree
		global name

		self.filename = filename
		name = os.path.basename(filename)
		self.iconfile = "/usr/lib/linuxmint/mintSystem/icon.png"

		# Set the Glade file
		self.gladefile = "/usr/lib/linuxmint/mintUpload/mintUpload.glade"
		wTree = gtk.glade.XML(self.gladefile,"main_window")

		wTree.get_widget("main_window").connect("destroy", gtk.main_quit)
		wTree.get_widget("main_window").set_icon_from_file(self.iconfile)

		# i18n
		wTree.get_widget("label2").set_label("<b>" + _("Upload service") + "</b>")
		wTree.get_widget("label3").set_label("<b>" + _("Local file") + "</b>")
		wTree.get_widget("label4").set_label("<b>" + _("Remote file") + "</b>")
		wTree.get_widget("label187").set_label(_("Name:"))
		wTree.get_widget("lbl_space").set_label(_("Free space:"))
		wTree.get_widget("lbl_maxsize").set_label(_("Max file size:"))
		wTree.get_widget("lbl_persistence").set_label(_("Persistence:"))
		wTree.get_widget("label195").set_label(_("Path:"))
		wTree.get_widget("label193").set_label(_("Size:"))
		wTree.get_widget("label190").set_label(_("Upload progress:"))
		wTree.get_widget("lbl_url").set_label(_("URL:"))

		fileMenu = gtk.MenuItem(_("_File"))
		fileSubmenu = gtk.Menu()
		fileMenu.set_submenu(fileSubmenu)
		closeMenuItem = gtk.ImageMenuItem(gtk.STOCK_CLOSE)
		closeMenuItem.get_child().set_text(_("Close"))
		closeMenuItem.connect("activate", gtk.main_quit)
		fileSubmenu.append(closeMenuItem)

		helpMenu = gtk.MenuItem(_("_Help"))
		helpSubmenu = gtk.Menu()
		helpMenu.set_submenu(helpSubmenu)
		aboutMenuItem = gtk.ImageMenuItem(gtk.STOCK_ABOUT)
		aboutMenuItem.get_child().set_text(_("About"))
		aboutMenuItem.connect("activate", self.open_about)
		helpSubmenu.append(aboutMenuItem)

		editMenu = gtk.MenuItem(_("_Edit"))
		editSubmenu = gtk.Menu()
		editMenu.set_submenu(editSubmenu)
		prefsMenuItem = gtk.ImageMenuItem(gtk.STOCK_PREFERENCES)
		prefsMenuItem.get_child().set_text(_("Services"))
		prefsMenuItem.connect("activate", self.open_services, wTree.get_widget("combo"))
		editSubmenu.append(prefsMenuItem)

		wTree.get_widget("menubar1").append(fileMenu)
		wTree.get_widget("menubar1").append(editMenu)
		wTree.get_widget("menubar1").append(helpMenu)
		wTree.get_widget("menubar1").show_all()

		self.reload_services(wTree.get_widget("combo"))

		cell = gtk.CellRendererText()
		wTree.get_widget("combo").pack_start(cell)
		wTree.get_widget("combo").add_attribute(cell,'text',0)

		wTree.get_widget("combo").connect("changed", self.comboChanged)
		wTree.get_widget("upload_button").connect("clicked", self.upload)
		wTree.get_widget("cancel_button").connect("clicked", gtk.main_quit)

		# Print the name of the file in the GUI
		wTree.get_widget("txt_file").set_label(self.filename)

		# Calculate the size of the file
		self.filesize = os.path.getsize(self.filename)
		wTree.get_widget("txt_size").set_label(sizeStr(self.filesize))

		if len(self.services) == 1:
			wTree.get_widget("combo").set_active(0)
			self.comboChanged(None)

	def reload_services(self, combo):
		model = gtk.TreeStore(str)
		combo.set_model(model)
		self.services = read_services()
		for service in self.services:
			iter = model.insert_before(None, None)
			model.set_value(iter, 0, service['name'])
		del model

	def open_about(self, widget):
		dlg = gtk.AboutDialog()
		dlg.set_title(_("About") + " - mintUpload")
		version = commands.getoutput("mint-apt-version mintupload")
		dlg.set_version(version)
		dlg.set_program_name("mintUpload")
		dlg.set_comments(_("File uploader for Linux Mint"))
		try:
		    h = open('/usr/lib/linuxmint/mintSystem/GPL.txt','r')
		    s = h.readlines()
		    gpl = ""
		    for line in s:
		    	gpl += line
		    h.close()
		    dlg.set_license(gpl)
		except Exception, detail:
		    print detail

		dlg.set_authors([
			"Clement Lefebvre <root@linuxmint.com>",
			"Philip Morrell <ubuntu.emorrp1@xoxy.net>",
			"Manuel Sandoval <manuel@slashvar.com>",
			"Dennis Schwertel <s@digitalkultur.net>"
		])
		dlg.set_icon_from_file(self.iconfile)
		dlg.set_logo(gtk.gdk.pixbuf_new_from_file(self.iconfile))
		def close(w, res):
		    if res == gtk.RESPONSE_CANCEL:
		        w.hide()
		dlg.connect("response", close)
		dlg.show()

	def open_services(self, widget, combo):
		wTree = gtk.glade.XML(self.gladefile, "services_window")
		treeview_services = wTree.get_widget("treeview_services")
		treeview_services_system = wTree.get_widget("treeview_services_system")

		wTree.get_widget("services_window").set_title(_("Services") + " - mintUpload")
		wTree.get_widget("services_window").set_icon_from_file(self.iconfile)
		wTree.get_widget("services_window").show()

		wTree.get_widget("button_close").connect("clicked", self.close_window, wTree.get_widget("services_window"), combo)
		wTree.get_widget("services_window").connect("destroy", self.close_window, wTree.get_widget("services_window"), combo)
		wTree.get_widget("toolbutton_add").connect("clicked", self.add_service, treeview_services)
		wTree.get_widget("toolbutton_edit").connect("clicked", self.edit_service_toolbutton, treeview_services)
		wTree.get_widget("toolbutton_remove").connect("clicked", self.remove_service, treeview_services)

		column1 = gtk.TreeViewColumn(_("Services"), gtk.CellRendererText(), text=0)
		column1.set_sort_column_id(0)
		column1.set_resizable(True)
		treeview_services.append_column(column1)
		treeview_services.set_headers_clickable(True)
		treeview_services.set_reorderable(False)
		treeview_services.show()
		column1 = gtk.TreeViewColumn(_("System-wide services"), gtk.CellRendererText(), text=0)
		treeview_services_system.append_column(column1)
		treeview_services_system.show()
		self.load_services(treeview_services, treeview_services_system)
		treeview_services.connect("row-activated", self.edit_service);

	def load_services(self, treeview_services, treeview_services_system):
		usermodel = gtk.TreeStore(str)
		usermodel.set_sort_column_id( 0, gtk.SORT_ASCENDING )
		sysmodel = gtk.TreeStore(str)
		sysmodel.set_sort_column_id( 0, gtk.SORT_ASCENDING )
		models = {
			'user':usermodel,
			'system':sysmodel
		}
		treeview_services.set_model(models['user'])
		treeview_services_system.set_model(models['system'])

		self.services = read_services()
		for service in self.services:
			iter = models[service['loc']].insert_before(None, None)
			models[service['loc']].set_value(iter, 0, service['name'])

		del usermodel
		del sysmodel

	def close_window(self, widget, window, combo=None):
		window.hide()
		if (combo != None):
			self.reload_services(combo)

	def add_service(self, widget, treeview_services):
		wTree = gtk.glade.XML(self.gladefile, "dialog_add_service")
		wTree.get_widget("dialog_add_service").set_title(_("New service"))
		wTree.get_widget("dialog_add_service").set_icon_from_file(self.iconfile)
		wTree.get_widget("dialog_add_service").show()
		wTree.get_widget("lbl_name").set_label(_("Name:"))
		wTree.get_widget("button_ok").connect("clicked", self.new_service, wTree.get_widget("dialog_add_service"), wTree.get_widget("txt_name"), treeview_services)
		wTree.get_widget("button_cancel").connect("clicked", self.close_window, wTree.get_widget("dialog_add_service"))

	def new_service(self, widget, window, entry, treeview_services):
		service = Service('/usr/lib/linuxmint/mintUpload/sample.service')
		sname = entry.get_text()
		if sname:
			model = treeview_services.get_model()
			iter = model.insert_before(None, None)
			model.set_value(iter, 0, sname)
			service.filename = home + "/.linuxmint/mintUpload/services/" + sname
			service.write()
		self.close_window(None, window)
		self.edit_service(treeview_services, model.get_path(iter), 0)

	def edit_service_toolbutton(self, widget, treeview_services):
		selection = treeview_services.get_selection()
		(model, iter) = selection.get_selected()
		self.edit_service(treeview_services, model.get_path(iter), 0)

	def edit_service(self,widget, path, column):
		model=widget.get_model()
		iter = model.get_iter(path)
		sname = model.get_value(iter, 0)
		file = home + "/.linuxmint/mintUpload/services/" + sname

		wTree = gtk.glade.XML(self.gladefile, "dialog_edit_service")
		wTree.get_widget("dialog_edit_service").set_title(_("Edit service"))
		wTree.get_widget("dialog_edit_service").set_icon_from_file(self.iconfile)
		wTree.get_widget("dialog_edit_service").show()
		wTree.get_widget("button_ok").connect("clicked", self.modify_service, wTree.get_widget("dialog_edit_service"), wTree, file)
		wTree.get_widget("button_cancel").connect("clicked", self.close_window, wTree.get_widget("dialog_edit_service"))

		#i18n
		wTree.get_widget("lbl_type").set_label(_("Type:"))
		wTree.get_widget("lbl_hostname").set_label(_("Hostname:"))
		wTree.get_widget("lbl_port").set_label(_("Port:"))
		wTree.get_widget("lbl_username").set_label(_("Username:"))
		wTree.get_widget("lbl_password").set_label(_("Password:"))
		wTree.get_widget("lbl_timestamp").set_label(_("Timestamp:"))
		wTree.get_widget("lbl_path").set_label(_("Path:"))

		wTree.get_widget("lbl_hostname").set_tooltip_text(_("Hostname or IP address, default: mint-space.com"))
		wTree.get_widget("txt_hostname").set_tooltip_text(_("Hostname or IP address, default: mint-space.com"))

		wTree.get_widget("lbl_port").set_tooltip_text(_("Remote port, default is 21 for FTP, 22 for SFTP and SCP"))
		wTree.get_widget("txt_port").set_tooltip_text(_("Remote port, default is 21 for FTP, 22 for SFTP and SCP"))

		wTree.get_widget("lbl_username").set_tooltip_text(_("Username, defaults to your local username"))
		wTree.get_widget("txt_username").set_tooltip_text(_("Username, defaults to your local username"))

		wTree.get_widget("lbl_password").set_tooltip_text(_("Password, by default: password-less SCP connection, null-string FTP connection, ~/.ssh keys used for SFTP connections"))
		wTree.get_widget("txt_password").set_tooltip_text(_("Password, by default: password-less SCP connection, null-string FTP connection, ~/.ssh keys used for SFTP connections"))

		wTree.get_widget("lbl_timestamp").set_tooltip_text(_("Timestamp format (strftime). By default:") + "%Y%m%d%H%M%S")
		wTree.get_widget("txt_timestamp").set_tooltip_text(_("Timestamp format (strftime). By default:") + "%Y%m%d%H%M%S")

		wTree.get_widget("lbl_path").set_tooltip_text(_("Directory to upload to. <TIMESTAMP> is replaced with the current timestamp, following the timestamp format given. By default: ."))
		wTree.get_widget("txt_path").set_tooltip_text(_("Directory to upload to. <TIMESTAMP> is replaced with the current timestamp, following the timestamp format given. By default: ."))

		try:
			config = Service(file)
			try:
				model = wTree.get_widget("combo_type").get_model()
				iter = model.get_iter_first()
				while (iter != None and model.get_value(iter, 0) != config['type'].lower()):
					iter = model.iter_next(iter)
				wTree.get_widget("combo_type").set_active_iter(iter)
			except:
				pass
			try:
				wTree.get_widget("txt_hostname").set_text(config['host'])
			except:
				wTree.get_widget("txt_hostname").set_text("")
			try:
				wTree.get_widget("txt_port").set_text(config['port'])
			except:
				wTree.get_widget("txt_port").set_text("")
			try:
				wTree.get_widget("txt_username").set_text(config['user'])
			except:
				wTree.get_widget("txt_username").set_text("")
			try:
				wTree.get_widget("txt_password").set_text(config['pass'])
			except:
				wTree.get_widget("txt_password").set_text("")
			try:
				wTree.get_widget("txt_timestamp").set_text(config['format'])
			except:
				wTree.get_widget("txt_timestamp").set_text("")
			try:
				wTree.get_widget("txt_path").set_text(config['path'])
			except:
				wTree.get_widget("txt_path").set_text("")
		except Exception, detail:
			print detail

	def modify_service(self, widget, window, wTree, file):
		try:
			model = wTree.get_widget("combo_type").get_model()
			iter = 	wTree.get_widget("combo_type").get_active_iter()

			# Get configuration
			config = {}
			config['type'] = model.get_value(iter, 0)
			config['host'] = wTree.get_widget("txt_hostname").get_text()
			config['port'] = wTree.get_widget("txt_port").get_text()
			config['user'] = wTree.get_widget("txt_username").get_text()
			config['pass'] = wTree.get_widget("txt_password").get_text()
			config['format'] = wTree.get_widget("txt_timestamp").get_text()
			config['path'] = wTree.get_widget("txt_path").get_text()

			# Write to service's config file
			s = Service(file)
			s.merge(config)
			s.write()
		except Exception, detail:
			print detail
		window.hide()

	def remove_service(self, widget, treeview_services):
		(model, iter) = treeview_services.get_selection().get_selected()
		if (iter != None):
			service = model.get_value(iter, 0).replace(' ', '\ ')
			os.system("rm " + home + "/.linuxmint/mintUpload/services/" + service)
			model.remove(iter)

	def comboChanged(self, widget):
		'''Change the selected service'''

		global progressbar
		global statusbar
		global wTree
		global context_id
		global selected_service

		progressbar = wTree.get_widget("progressbar")
		statusbar = wTree.get_widget("statusbar")
		context_id = statusbar.get_context_id("mintUpload")

		# Get the selected service
		model = wTree.get_widget("combo").get_model()
		active = wTree.get_widget("combo").get_active()
		if active < 0:
			return
		selectedService = model[active][0]

		self.services = read_services()
		for service in self.services:
			if service['name'] == selectedService:
				selected_service = service

				# Get the file's persistence on the service
				if selected_service.has_key('persistence'):
					wTree.get_widget("txt_persistence").set_label(str(selected_service['persistence']) + " " + _("days"))
					wTree.get_widget("txt_persistence").show()
					wTree.get_widget("lbl_persistence").show()
				else:
					wTree.get_widget("txt_persistence").set_label(_("N/A"))
					wTree.get_widget("txt_persistence").hide()
					wTree.get_widget("lbl_persistence").hide()

				# Get the maximum allowed filesize on the service
				if selected_service.has_key('maxsize'):
					maxsizeStr = sizeStr(selected_service['maxsize'])
					wTree.get_widget("txt_maxsize").set_label(maxsizeStr)
					wTree.get_widget("txt_maxsize").show()
					wTree.get_widget("lbl_maxsize").show()
				else:
					wTree.get_widget("txt_maxsize").set_label(_("N/A"))
					wTree.get_widget("txt_maxsize").hide()
					wTree.get_widget("lbl_maxsize").hide()

				if not selected_service.has_key('space'):
					wTree.get_widget("txt_space").set_label(_("N/A"))
					wTree.get_widget("txt_space").hide()
					wTree.get_widget("lbl_space").hide()
					if not selected_service.has_key('maxsize'):
						return True

				self.check_space()
				return True

	def check_space(self):
		'''Checks for available space on the service'''

		global statusbar
		global context_id
		global wTree
		global selected_service

		wTree.get_widget("main_window").window.set_cursor(gtk.gdk.Cursor(gtk.gdk.WATCH))
		wTree.get_widget("combo").set_sensitive(False)
		wTree.get_widget("upload_button").set_sensitive(False)
		statusbar.push(context_id, _("Checking space on the service..."))

		wTree.get_widget("frame_progress").hide()

		# Check the filesize
		try:
			spacecheck = spaceChecker(selected_service, self.filesize)
			spacecheck.start()
			spacecheck.join()

		except ConnectionError:
			statusbar.push(context_id, "<span color='red'>" + _("Could not connect to the service.") + "</span>")

		except FilesizeError:
			statusbar.push(context_id, "<span color='red'>" + _("File too big or not enough space on the service.") + "</span>")

		else:
			# Display the available space left on the service
			if selected_service.has_key('space'):
				pctSpace = float(spacecheck.available) / float(spacecheck.total) * 100
				pctSpaceStr = sizeStr(spacecheck.available) + " (" + str(int(pctSpace)) + "%)"
				wTree.get_widget("txt_space").set_label(pctSpaceStr)
				wTree.get_widget("txt_space").show()
				wTree.get_widget("lbl_space").show()

			# Activate upload button
			statusbar.push(context_id, "<span color='green'>" + _("Service ready. Space available.") + "</span>")
			wTree.get_widget("upload_button").set_sensitive(True)

		finally:
			label = statusbar.get_children()[0].get_children()[0]
			label.set_use_markup(True)
			wTree.get_widget("combo").set_sensitive(True)
			wTree.get_widget("main_window").window.set_cursor(None)
			wTree.get_widget("main_window").resize(*wTree.get_widget("main_window").size_request())

	def upload(self, widget):
		'''Start the upload process'''

		global wTree
		global selected_service

		wTree.get_widget("upload_button").set_sensitive(False)
		wTree.get_widget("combo").set_sensitive(False)
		selected_service = selected_service.for_upload(self.filename)
		uploader = mintUploader()
		uploader.start()
		return True

def read_services():
	'''Get all defined services'''

	services = []
	config_paths = {'system':"/etc/linuxmint/mintUpload/services/", 'user':home + "/.linuxmint/mintUpload/services/"}
	for loc, path in config_paths.iteritems():
		os.system("mkdir -p " + path)
		for file in os.listdir(path):
			try:
				s = Service(path + file)
			except:
				pass
			else:
				s['loc'] = loc
				services.append(s)
	return services

defaults = ConfigObj({
	'type':'MINT',
	'host':'mint-space.com',
	'user':os.environ['LOGNAME'],
	'path':'',
	'pass':'',
	'format':'%Y%m%d%H%M%S',
})

class Service(ConfigObj):
	'''Object representing an upload service'''

	def __init__(self, *args):
		'''Get the details of an individual service'''

		ConfigObj.__init__(self, *args)
		self._fix()

	def merge(self, *args):
		'''Merge configuration with another'''

		ConfigObj.merge(self, *args)
		self._fix()

	def _fix(self):
		'''Format values correctly'''

		for k,v in self.iteritems():
			if v:
				if type(v) is list:
					self[k] = ','.join(v)
			else:
				self.pop(k)

		if self.filename:
			self['name'] = os.path.basename(self.filename)

		if self.has_key('type'):
			self['type'] = self['type'].upper()

		if self.has_key('host'):
			h = self['host']
			if h.find(':') >= 0:
				h = h.split(':')
				self['host'] = h[0]
				self['port'] = h[1]

		ints = ['port', 'maxsize', 'persistence']
		for k in ints:
			if self.has_key(k):
				self[k] = int(self[k])

	def for_upload(self, file):
		'''Upload a file to the service'''

		s = defaults
		s.merge(self)

		timestamp = datetime.datetime.utcnow().strftime(s['format'])
		s['path'] = s['path'].replace('<TIMESTAMP>',timestamp)

		# Replace placeholders in url
		url_replace = {
			'<TIMESTAMP>':timestamp,
			'<FILE>':os.path.basename(file),
			'<PATH>':s['path']
		}
		url = s['url']
		for k,v in url_replace.iteritems():
			url = url.replace(k,v)
		s['url'] = url

		# Ensure trailing '/', after url <PATH> replace
		if s['path']:
			s['path'] = os.path.normpath(s['path'])
		else:
			s['path'] = os.curdir
		s['path'] += os.sep

		return s

def my_storbinary(self, cmd, fp, blocksize=8192, callback=None):
	'''Store a file in binary mode.'''

	self.voidcmd('TYPE I')
	conn = self.transfercmd(cmd)
	while 1:
		buf = fp.read(blocksize)
		if not buf: break
		conn.sendall(buf)
		if callback: callback(buf)
	conn.close()
	return self.voidresp()

def my_storlines(self, cmd, fp, callback=None):
	'''Store a file in line mode.'''

	self.voidcmd('TYPE A')
	conn = self.transfercmd(cmd)
	while 1:
		buf = fp.readline()
		if not buf: break
		if buf[-2:] != CRLF:
			if buf[-1] in CRLF: buf = buf[:-1]
			buf = buf + CRLF
		conn.sendall(buf)
		if callback: callback(buf)
	conn.close()
	return self.voidresp()

# Use the patched versions
ftplib.FTP.storbinary = my_storbinary
ftplib.FTP.storlines = my_storlines

if __name__ == "__main__":
	filename = sys.argv[1]
	mainwin = mintUploadWindow(filename)
	gtk.main()
