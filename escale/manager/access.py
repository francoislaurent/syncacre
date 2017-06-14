# -*- coding: utf-8 -*-

# Copyright © 2017, François Laurent

# This file is part of the Escale software available at
# "https://github.com/francoislaurent/escale" and is distributed under
# the terms of the CeCILL-B license as circulated at the following URL
# "http://www.cecill.info/licenses.en.html".

# The fact that you are presently reading this means that you have had
# knowledge of the CeCILL-B license and that you accept its terms.


from escale.base.essential import *
import os
import itertools


if PYTHON_VERSION == 2:
	#import gdbm as dbm
	import anydbm as dbm
	class PermissionError(OSError):
		pass
	def asbinary(s):
		if isinstance(s, unicode):
			return s.encode('utf-8')
		else:
			return s
else:
	#import dbm.gnu as dbm
	import dbm
	def asbinary(s):
		if isinstance(s, str):
			return s.encode('utf-8')
		else:
			return s


access_modifier_prefix = 'am'


class Accessor(object):
	"""
	Interface to a single local file.

	This class aims at hiding the actual file so that access to it is 
	strictly controlled, while still featuring basic file manipulation 
	methods.
	"""
	__slots__ = ['exists', 'delete']
	def __init__(self, exists=None, delete=None):
		self.exists = exists
		self.delete = delete


class TableEntry(object):

	__slots__ = [ 'default', 'table', 'key' ]

	def __init__(self, table, key, default=None):
		self.table = table
		self.key = asbinary(key)
		self.default = default

	def __enter__(self):
		self.table = dbm.open(self.table, 'c')
		return self

	def __exit__(self, *args):
		self.table.close()

	def get(self):
		try: # Python2 does not implement the `get` method of the `dict` interface
			value = self.table[self.key]
		except KeyError:
			value = self.default
		return value

	def delete(self):
		del self.table[self.key]

	def set(self, value):
		self.table[self.key] = value


class AccessAttributes(object):

	__slots__ = [ '_r', '_w', '_u', '_undefined', '_t', '_f', 'location', '_table' ]

	def __init__(self, location=None, dbm_mode='c'):
		self._r = 0
		self._w = 1
		self._u = b' '
		self._t = b'+'
		self._f = b'-'
		self._undefined = b'  '
		self._table = None
		self.location = location
		#if self.location:
		#	self._table = dbm.open(self.location, dbm_mode)
			

	def __nonzero__(self):
		return self.location is not None

	def _decode(self, a):
		if a is self._u:
			return None
		elif a is self._f:
			return False
		elif a is self._t:
			return True
		else:
			raise ValueError("unrecognized symbol '{}'".format(a))

	def _encode(self, a):
		if a is False:
			return self._f
		elif a is True:
			return self._t
		elif a is None:
			return self._u
		else:
			raise ValueError("unsupported value '{}'".format(a))

	def table(self, resource):
		return TableEntry(self.location, resource, self._undefined)

	def __contains__(self, resource):
		if self.location:
			with TableEntry(self.location, resource, None) as e:
				return e.get() is not None
		else:
			return False

	def _ability(self, attr, resource, explicit=None):
		if self.location is None:
			return None
		else:
			with self.table(resource) as entry:
				attributes = entry.get()
				value = self._decode(attributes[attr])
				if value is None and explicit:
					value = explicit
					attributes = self._encode(value).join((attributes[:attr], attributes[attr+1:]))
					entry.set(attributes)
				return value

	def _is(self, attr, resource, make_explicit=None):
		if make_explicit is None:
			return self._ability(attr, resource) is not False
		else:
			return self._ability(attr, resource, explicit=make_explicit)
		
	def isReadable(self, resource, make_explicit=None):
		"""
		Determine whether a resource can be uploaded.
		"""
		return self._is(self._r, resource, make_explicit=make_explicit)

	def isWritable(self, resource, make_explicit=None):
		"""
		Determine whether a resource can be downloaded.
		"""
		return self._is(self._w, resource, make_explicit=make_explicit)

	def getReadability(self, resource):
		return self._ability(self._r, resource)

	def getWritability(self, resource):
		return self._ability(self._w, resource)

	def _set(self, attr, resource, perm):
		with self.table(resource) as entry:
			attributes = entry.get()
			attributes = self._encode(perm).join((attributes[:attr], attributes[attr+1:]))
			if attributes is self._undefined:
				entry.delete()
			else:
				entry.set(attributes)

	def setReadable(self, resource):
		self._set(self._r, resource, True)

	def setNotReadable(self, resource):
		self._set(self._r, resource, False)

	def unsetReadability(self, resource):
		self._set(self._r, resource, None)

	def setReadability(self, resource, r):
		self._set(self._r, resource, r)

	def setWritable(self, resource):
		self._set(self._w, resource, True)

	def setNotWritable(self, resource):
		self._set(self._w, resource, False)

	def unsetWritability(self, resource):
		self._set(self._w, resource, None)

	def setWritability(self, resource, w):
		self._set(self._w, resource, w)



class AccessController(Reporter):
	"""
	Manage access to the files in a local repository.

	Attributes:

		persistent (str): path to persistent data.

		repository (str): repository identifier.

		path (str): path to repository root.

		mode (str): any of `download`, `upload`, `conservative` or `share`/`shared` (default).

		create (str): if ``True`` create persistent data if missing.

	When `push_only` (resp. `pull_only`) is ``True`` , `mode` is `upload` (resp. `download`).

	When `mode` is `download`, `upload` or `shared`, and the persistent attributes do not exist
	at init time (database not created), any external change (e.g. with `escalectl`) will not 
	be taken into account, unless `create` is ``True``.
	"""
	def __init__(self, repository, path=None,
			persistent=None,
			ui_controller=None,
			push_only=False, pull_only=False,
			mode=None, create=False,
			**ignored):
		Reporter.__init__(self, ui_controller=ui_controller)
		self.name = repository
		if not path:
			msg = 'no local repository defined'
			self.logger.error(msg)
			raise KeyError(msg)
		if path[-1] != '/':
			path += '/'
		self.path = os.path.normpath(asstr(path))
		# set operating mode
		self.mode = None
		if push_only:
			if pull_only:
				msg = 'both read only and write only; cannot determine mode'
				self.logger.error(msg)
				raise KeyError(msg)
			else:
				self.mode = 'upload'
		elif pull_only:
			self.mode = 'download'
		elif mode:
			self.mode = mode
		# set persistent data
		self.persistent = AccessAttributes()
		if persistent:
			if create or self.mode == 'conservative' or os.path.exists(persistent):
				if not os.path.exists(persistent):
					dirname = os.path.dirname(persistent)
					if not os.path.isdir(dirname):
						os.makedirs(dirname)
				self.persistent = AccessAttributes(persistent)

	@property
	def mode(self):
		return self._mode

	@mode.setter
	def mode(self, m):
		if m is None:
			self._mode = None
		else:
			m = m.lower()
			if m in [ 'download', 'upload' ]:
				self._mode = m
			elif m.startswith('shar'): # 'share', 'shared', 'sharing', etc
				self._mode = 'shared'
			elif m in [ 'conservative', 'protective' ]:
				self._mode = 'conservative'
			else:
				raise ValueError("'{}' mode not supported".format(m))

	def listFiles(self, path=None):
		"""
		List all visible files in the local repository.

		Files which name begins with "." are ignored.
		"""
		if path is None:
			path = self.path
		ls = [ os.path.join(path, f) for f in os.listdir(path) if f[0] != '.' ]
		local = itertools.chain([ f for f in ls if os.path.isfile(f) ], \
			*[ self.listFiles(f) for f in ls if os.path.isdir(f) ])
		return list(local)

	def readable(self, files):
		"""
		Select filenames from the repository that can be uploaded.

		Arguments:

			files (list): list of relative paths.

		Returns:

			list: uploadable filenames.
		"""
		if self.mode == 'download': # in principle `download` should not call `readable`
			files = []
		elif self.persistent is not None:
			# assert that all files are in the repository with __safe__ (even those that
			# are not readable) and filter readable files
			files = [ f for f in files if self.__safe__(self.persistent.isReadable, f) ]
		return files

	def writable(self, filename):
		"""
		Get the local path corresponding to a remote resource if it can be downloaded.

		Arguments:

			filename (str): path to remote file.

		Returns:

			str or None: local absolute path, or ``None`` if the local resource is not writable.
		"""
		if self.mode == 'upload':
			return None
		else:
			r, f = self._format(filename, True)
			w = None
			if self.mode == 'conservative':
				if os.path.exists(f):
					w = False
			if self.persistent is None or self.persistent.isWritable(r, make_explicit=w):
				return f
			else:
				return None

	def accessor(self, filename):
		local_file = join(self.path, filename)
		if os.path.isfile(local_file):
			def exists():
				return True
			if self.mode == 'download':
				def delete():
					os.unlink(local_file)
			else:
				def delete():
					raise PermissionError(local_file)
		else:
			def exists():
				return False
			def delete():
				pass
		return Accessor(exists=exists, delete=delete)

	def _format(self, filename, return_absolute_path=False):
		"""
		Format filename.
		"""
		filename = asstr(filename)
		if os.path.isabs(filename):
			abspath = filename
			relpath = os.path.relpath(filename, self.path)
			if relpath.startswith(os.pardir):
				# file is not in the repository
				relpath = None
		else:
			if return_absolute_path:
				abspath = os.path.join(self.path, filename)
			relpath = filename
		if return_absolute_path:
			return (relpath, abspath)
		else:
			return relpath

	def __safe__(self, access, filename, *args):
		"""
		Format filename and require that the file does exist in the local repository.

		A callback is called with the formated filename as first input argument if file
		exists, otherwise `OSError` is raised.
		"""
		relpath, abspath = self._format(filename, True)
		if relpath and not os.path.exists(abspath):
			if relpath in self.persistent:
				self.logger.debug("cannot find file '%s' in the filesystem", abspath)
			else:
				relpath = None
		if relpath:
			return access(relpath, *args)
		else:
			raise OSError("cannot find file '{}' in repository '{}'".format(filename, self.name))

	def getReadability(self, filename):
		return self.persistent.getReadability(self._format(filename))

	def getWritability(self, filename):
		return self.persistent.getWritability(self._format(filename))

	def setReadability(self, filename, r):
		self.__safe__(self.persistent.setReadability, filename, r)

	def setWritability(self, filename, w):
		self.__safe__(self.persistent.setWritability, filename, w)

	def confirmPull(self, filename):
		if self.mode == 'conservative':
			self.persistent.setWritable(self._format(filename))

	def confirmPush(self, filename):
		if self.mode == 'conservative':
			self.__safe__(self.persistent.setNotWritable, filename)
