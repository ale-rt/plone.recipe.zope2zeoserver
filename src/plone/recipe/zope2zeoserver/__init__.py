##############################################################################
#
# Copyright (c) 2006-2007 Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################

import sys, os, re, shutil
import zc.buildout
import zc.recipe.egg

class Recipe:

    def __init__(self, buildout, name, options):
        self.egg = zc.recipe.egg.Egg(buildout, options['recipe'], options)
        self.buildout, self.options, self.name = buildout, options, name

        options['location'] = os.path.join(
            buildout['buildout']['parts-directory'],
            self.name,
            )
        options['bin-directory'] = buildout['buildout']['bin-directory']
        options['scripts'] = '' # suppress script generation.

        _, self.zodb_ws = self.egg.working_set()

    _ws_locations = None

    @property
    def ws_locations(self):
        if self._ws_locations is None:
            self._ws_locations = [d.location for d in self.zodb_ws]
            # account for zope2-location, if provided
            if self.options.get('zope2-location') is not None:
                software_home = os.path.join(self.options['zope2-location'],
                                             'lib', 'python')
                assert os.path.isdir(software_home), invalid_z2_location_msg
                self._ws_locations.append(software_home)
        return self._ws_locations

    def install(self):
        options = self.options
        location = options['location']

        # evil hack alert!
        # hopefully place ZODB in sys.path
        orig_sys_path = tuple(sys.path)
        sys.path[0:0] = self.ws_locations
        try:
            # if we can import ZEO, assume the depenencies (zdaemon, ZConfig,
            # etc...) are also present on 'path'
            import ZEO
        except ImportError:
            raise AssertionError(zodb_import_msg)

        if os.path.exists(location):
            shutil.rmtree(location)

        # What follows is a bit of a hack because the instance-setup mechanism
        # is a bit monolithic. We'll run mkzeoinst and then we'll
        # patch the result. A better approach might be to provide independent
        # instance-creation logic, but this raises lots of issues that
        # need to be stored out first.
        
        # this was taken from mkzeoinstance.py
        from ZEO.mkzeoinst import ZEOInstanceBuilder
        
        zodb3_home = os.path.dirname(os.path.dirname(ZEO.__file__))
        params = {
            "package": "zeo",
            "PACKAGE": "ZEO",
            "zodb3_home": zodb3_home,
            "instance_home": location,
            "port": 8100, # will be overwritten later
            "python": options['executable'],
            }
        ZEOInstanceBuilder().create(location, params)

        try:
            # Save the working set:
            open(os.path.join(location, 'etc', '.eggs'), 'w').write(
                '\n'.join(self.ws_locations))

            # Make a new zeo.conf based on options in buildout.cfg
            self.build_zeo_conf()

            # Patch extra paths into binaries
            self.patch_binaries()

            # Install extra scripts
            self.install_scripts()

        except:
            # clean up
            shutil.rmtree(location)
            raise

        # end evil hack and restore sys.path to it's glorious days
        del sys.path[0:len(self.ws_locations)]
        assert tuple(sys.path) == orig_sys_path

        return location

    def update(self):
        options = self.options
        location = options['location']

        if os.path.exists(location):
            # See if we can stop. We need to see if the working set path
            # has changed.
            saved_path = os.path.join(location, 'etc', '.eggs')
            if os.path.isfile(saved_path):
                if (open(saved_path).read() !=
                    '\n'.join(self.ws_locations)
                    ):
                    # Something has changed. Blow away the instance.
                    self.install()

            # Nothing has changed.
            return location

        else:
            self.install()

        return location

    def build_zeo_conf(self):
        """Create a zeo.conf file
        """
        options = self.options
        location = options['location']
        instance_home = location

        zeo_conf_path = options.get('zeo-conf', None)
        if zeo_conf_path is not None:
            zeo_conf = "%%include %s" % os.path.abspath(zeo_conf_path)
        else:
            zeo_address = options.get('zeo-address', '8100')
            zeo_conf_additional = options.get('zeo-conf-additional', '')
            storage_number = options.get('storage-number', '1')

            effective_user = options.get('effective-user', '')
            if effective_user:
               effective_user = 'user %s' % effective_user

            invalidation_queue_size = options.get('invalidation-queue-size',
                                                  '100')

            base_dir = self.buildout['buildout']['directory']
            socket_name = options.get('socket-name',
                                      '%s/var/zeo.zdsock' % base_dir)
            socket_dir = os.path.dirname(socket_name)
            if not os.path.exists(socket_dir):
                os.makedirs(socket_dir)

            z_log_name = os.path.sep.join(('var', 'log', self.name + '.log',))
            zeo_log_custom = options.get('zeo-log-custom', None)
            
            # if zeo-log is given, we use it to set the runner
            # logfile value in any case
            z_log_filename = options.get('zeo-log', z_log_name)
            z_log_filename = os.path.join(base_dir, z_log_filename)
            z_log_dir = os.path.dirname(z_log_filename)
            if not os.path.exists(z_log_dir):
                os.makedirs(z_log_dir)

            # zeo-log-custom superseeds zeo-log
            if zeo_log_custom is None:
                z_log = z_log_file % {'filename': z_log_filename}
            else:
                z_log = zeo_log_custom

            file_storage = os.path.sep.join(('var', 'filestorage', 'Data.fs',))
            file_storage = options.get('file-storage', file_storage)
            file_storage = os.path.join(base_dir, file_storage)
            file_storage_dir = os.path.dirname(file_storage)
            if not os.path.exists(file_storage_dir):
                os.makedirs(file_storage_dir)

            pid_file = options.get(
                'pid-file',
                os.path.join(base_dir, 'var', self.name + '.pid'))

            # check whether we'll wrap a blob storage around our file storage
            blob_storage = options.get('blob-storage', None)
            if blob_storage is not None:
                storage_template = blob_storage_template
            else:
                storage_template = file_storage_template

            storage = storage_template % dict(
                storage_number = storage_number,
                file_storage = file_storage,
                blob_storage = blob_storage
                )

            zeo_conf = zeo_conf_template % dict(
                instance_home = instance_home,
                effective_user = effective_user,
                invalidation_queue_size = invalidation_queue_size,
                socket_name = socket_name,
                z_log = z_log,
                z_log_filename = z_log_filename,
                storage = storage,
                zeo_address = zeo_address,
                pid_file = pid_file,
                zeo_conf_additional = zeo_conf_additional,
                )

        zeo_conf_path = os.path.join(location, 'etc', 'zeo.conf')
        open(zeo_conf_path, 'w').write(zeo_conf)

    def patch_binaries(self):
        location = self.options['location']
        # XXX We need to patch the windows specific batch scripts
        # and they need a different path seperator
        path = os.path.pathsep.join(self.ws_locations)
        for script_name in ('runzeo', 'zeoctl'):
            script_path = os.path.join(location, 'bin', script_name)
            script = open(script_path).read()
            script = script.replace('PYTHONPATH="$ZODB3_HOME"',
                                    'PYTHONPATH="%s"' % path)
            f = open(script_path, 'w')
            f.write(script)
            f.close()

    def install_scripts(self):
        options = self.options
        location = options['location']

        zeo_conf = options.get('zeo-conf', None)
        if zeo_conf is None:
            zeo_conf = os.path.join(location, 'etc', 'zeo.conf')

        _, ws = self.egg.working_set(['plone.recipe.zope2zeoserver'])

        initialization = """
        import os; os.environ['PYTHONPATH'] = %r
        """.strip() % os.path.pathsep.join(self.ws_locations)
        zc.buildout.easy_install.scripts(
            [(self.name, 'plone.recipe.zope2zeoserver.ctl', 'main')],
            ws, options['executable'], options['bin-directory'],
            extra_paths = self.ws_locations,
            initialization = initialization,
            arguments = ('\n        ["-C", %r]'
                         '\n        + sys.argv[1:]'
                         % zeo_conf
                         ),
            )
        # zeopack.py
        zeopack = options.get('zeopack', None)
        if zeopack is not None:
            directory, filename = os.path.split(zeopack)
            if zeopack and os.path.exists(zeopack):
                zc.buildout.easy_install.scripts(
                    [('zeopack', os.path.splitext(filename)[0], 'main')],
                    {}, options['executable'], options['bin-directory'],
                    extra_paths = self.ws_locations + [directory],
                    )
        else:
            zeo_address = options.get('zeo-address', '8100')
            parts = zeo_address.split(':')
            if len(parts) == 1:
                parts[0:0] = ['127.0.0.1']
            zc.buildout.easy_install.scripts(
                [('zeopack', 'plone.recipe.zope2zeoserver.pack', 'main')],
                self.zodb_ws, options['executable'], options['bin-directory'],
                initialization='host = "%s"\nport = %s' % tuple(parts),
                arguments='host, port',
                )

invalid_z2_location_msg = """
'zope2-location' doesn't point to an actual Zope2 SOFTWARE_HOME. Check this
setting and, eventually, make sure this buildout part is being run after the
part that is supposed to provide the 'zope2-location' value.
""".strip()

zodb_import_msg = """
Unable to import ZEO. Please, either add the ZODB3 egg to the 'eggs' entry or
set zope2-location
""".strip()

# the template used to build a regular file storage entry for zeo.conf
file_storage_template = """
<filestorage %(storage_number)s>
  path %(file_storage)s
</filestorage>
""".strip()

# the template used to build a blob storage wrapping a file storage entry for
# zeo.conf
blob_storage_template = """
<blobstorage %(storage_number)s>
  blob-dir %(blob_storage)s
  <filestorage %(storage_number)s>
    path %(file_storage)s
  </filestorage>
</blobstorage>
""".strip()

z_log_file = """\
     <logfile>
      path %(filename)s
      format %%(message)s
    </logfile>
""".strip()

# The template used to build zeo.conf
zeo_conf_template="""\
%%define INSTANCE %(instance_home)s

<zeo>
  address %(zeo_address)s
  read-only false
  invalidation-queue-size %(invalidation_queue_size)s
  pid-filename %(pid_file)s
</zeo>

%(storage)s

<eventlog>
  level info
  %(z_log)s
</eventlog>

<runner>
  program $INSTANCE/bin/runzeo
  socket-name %(socket_name)s
  daemon true
  forever false
  backoff-limit 10
  exit-codes 0, 2
  directory $INSTANCE
  default-to-interactive true
  %(effective_user)s

  # This logfile should match the one in the zeo.conf file.
  # It is used by zdctl's logtail command, zdrun/zdctl doesn't write it.
  logfile %(z_log_filename)s
</runner>

%(zeo_conf_additional)s
"""
