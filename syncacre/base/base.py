# -*- coding: utf-8 -*-

# Copyright (c) 2017, François Laurent

# Copyright (c) 2017, Institut Pasteur
#   Contributor: François Laurent
#   Contribution: UnrecoverableError


import logging, logging.handlers, logging.config
from multiprocessing import Process, Queue, Pipe
import threading
try:
	from configparser import NoOptionError
except ImportError:
	from ConfigParser import NoOptionError

from .exceptions import *
from .essential import *
from .config import *
from syncacre.log import *
import syncacre.relay as relay
from syncacre.manager import Manager, PermissionController
import syncacre.encryption as encryption
from syncacre.cli.controller import DirectController, UIController



def syncacre(config, repository, log_handler=None, ui_connector=None):
	"""
	Reads the section related to a repository in a loaded configuration object and runs a 
	:class:`~syncacre.manager.Manager` for that repository.

	Arguments:

		config (ConfigParser): configuration object.

		repository (str): configuration section name or, alternatively, client name.

		log_handler (log handler): input argument to :meth:`~logging.Logger.addHandler`.

		ui_connector (?): connector to user-interface controller.
	"""
	# set logger
	logger = logging.getLogger(log_root).getChild(repository)
	logger.setLevel(logging.DEBUG)
	if log_handler is not None:
		logger.propagate = False
		logger.addHandler(log_handler)
	# ui
	if ui_connector is None:
		ui_controller = DirectController(logger=logger)
	else:
		ui_controller = UIController(*ui_connector)
		ui_controller.logger = logger
	# parse config
	args = parse_fields(config, repository, fields, logger)
	# local repository
	lr_controller = PermissionController(repository,
			rundir=get_run_dir(config, repository),
			ui_controller=ui_controller, **args)
	# remote repository
	try:
		_protocol, args['address'], _port, _directory = parse_address(args['address'])
	except KeyError:
		msg = 'no address defined'
		logger.error(msg)
		raise KeyError(msg)
	try:
		protocol = config.get(repository, 'protocol')
	except NoOptionError:
		protocol = _protocol
	if not protocol:
		msg = 'no protocol defined'
		logger.error(msg)
		raise KeyError(msg)
	if _port:
		if 'port' in args:
			if args['port'] != port:
					logger.debug('conflicting port values: {}, {}'.format(port, args['port']))
		else:
			args['port'] = _port
	if _directory:
		if 'directory' in args:
			args['directory'] = os.path.join(args['directory'], _directory)
		else:
			args['directory'] = _directory
	# get credential
	if 'password' in args and os.path.isfile(args['password']):
		with open(args['password'], 'r') as f:
			content = f.readlines()
		content = [ line[:-1] if line[-1] == "\n" else line for line in content ]
		if 'username' in args:
			if not content[1:]:
				args['password'] = content[0]
			else:
				ok = False
				for line in content:
					if line.startswith(args['username']):
						args['password'] = line[len(args['username'])+1:]
						ok = True
						break
				if not ok:
					logger.error("cannot read password for user '%s' from file '%s'", args['username'], args[ 'password'])
					del args['password']
		else:
			try:
				args['username'], args['password'] = content[0].split(':', 1)
			except ValueError:
				logger.error("cannot read login information from credential file '%s'", args['password'])
				del args['password']
	# parse encryption algorithm and passphrase
	if 'passphrase' in args and os.path.isfile(args['passphrase']):
		with open(args['passphrase'], 'rb') as f:
			args['passphrase'] = f.read()
	if 'encryption' in args:
		if isinstance(args['encryption'], bool):
			if args['encryption']:
				cipher = encryption.by_cipher('fernet')
			else:
				cipher = None
		else:
			try:
				cipher = encryption.by_cipher(args['encryption'])
			except KeyError:
				cipher = None
				msg = "unsupported encryption algorithm '{}'".format(args['encryption'])
				logger.warning(msg)
				# do not let the user send plain data if she requested encryption:
				raise ValueError(msg)
		if cipher is not None and 'passphrase' not in args:
			cipher = None
			msg = 'missing passphrase; cannot encrypt'
			logger.warning(msg)
			# again, do not let the user send plain data if she requested encryption:
			raise ValueError(msg)
		if cipher is None:
			del args['encryption']
		else:
			args['encryption'] = cipher(args['passphrase'])
	# extra UI options
	ui_controller.maintainer = args.pop('maintainer', None)
	# ready
	if PYTHON_VERSION == 3:
		args['config'] = config[repository]
	elif PYTHON_VERSION == 2:
		args['config'] = (config, repository)
	manager = Manager(relay.by_protocol(protocol), protocol=protocol,
			ui_controller=ui_controller, repository=lr_controller, **args)
	try:
		result = manager.run()
	except (KeyboardInterrupt, SystemExit):
		raise
	except Exception as exc:
		if not ui_controller.failure(repository, exc):
			raise
	else:
		ui_controller.success(repository, result)



def syncacre_launcher(cfg_file, msgs=[], verbosity=logging.NOTSET, keep_alive=False):
	"""
	Parses a configuration file, sets the logger and launches the clients in separate subprocesses.

	Arguments:

		cfg_file (str): path to a configuration file.

		msgs (list): list of pending messages (`str` or `tuple`).

		verbosity (bool or int): verbosity level.

		keep_alive (bool or int): if ``True`` or non-negative `int`, clients are ran again 
			after they hit an unrecoverable error; 
			multiple threads and subprocesses are started even if a single client is defined;
			if `int`, specifies default sleep time after a subprocess crashed.

	"""
	if isinstance(keep_alive, bool):
		if keep_alive:
			restart_delay = 0
	elif isinstance(keep_alive, int):
		restart_delay = keep_alive
		keep_alive = True
	# parse the config file
	config, cfg_file, msgs = parse_cfg(cfg_file, msgs)
	# configure logger
	logger, msgs = set_logger(config, cfg_file, verbosity, msgs)
	# flush messages
	for msg in msgs:
		if isinstance(msg, tuple):
			if isinstance(msg[0], str):
				logger.warning(*msg)
			else: # msg[0] is log level
				logger.log(*msg)
		else:
			logger.warning(msg)
	# launch each client
	sections = config.sections()
	if sections[1:] or keep_alive: # if multiple sections
		if PYTHON_VERSION == 3:
			log_queue = Queue()
			log_listener = QueueListener(log_queue)
			log_handler = logging.handlers.QueueHandler(log_queue)
		elif PYTHON_VERSION == 2:
			import syncacre.log.socket as socket
			log_conn = ('localhost', logging.handlers.DEFAULT_TCP_LOGGING_PORT)
			log_listener = socket.SocketListener(*log_conn)
			log_handler = logging.handlers.SocketHandler(*log_conn)
		# logger
		logger_thread = threading.Thread(target=log_listener.listen)
		logger_thread.start()
		# result handling
		result_queue = Queue()
		# user interface
		ui_controller = UIController(logger=logger, parent=result_queue)
		ui_thread = threading.Thread(target=ui_controller.listen)
		ui_thread.start()
		# syncacre subprocesses
		workers = {}
		for section in config.sections():
			worker = Process(target=syncacre,
				name='{}.{}'.format(log_root, section),
				args=(config, section, log_handler, ui_controller.conn))
			workers[section] = worker
			worker.start()
		# wait for everyone to terminate
		try:
			if keep_alive:
				active_workers = len(workers)
				while 0 < active_workers:
					section, result = result_queue.get()
					if isinstance(result, Exception) and keep_alive:
						workers[section].terminate()
						# restart worker
						worker = Process(target=syncacre,
							name='{}.{}'.format(log_root, section),
							args=(config, section, log_handler,
								ui_controller.conn))
						workers[section] = worker
						ui_controller.restartWorker(section, restart_delay)
						worker.start()
					else:
						active_workers -= 1
			else:
				for worker in workers.values():
					worker.join()
		except (KeyboardInterrupt, SystemExit):
			for section, worker in workers.items():
				try:
					worker.terminate()
				except Exception as e:
					# 'NoneType' object has no attribute 'terminate'
					logger.warning("[%s]: %s", section, e)
					logger.debug("%s", workers)
		ui_controller.abort()
		log_listener.abort()
		ui_thread.join()
		logger_thread.join()
	else:
		syncacre(config, sections[0])


