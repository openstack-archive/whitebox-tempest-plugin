# Copyright 2016
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from contextlib import contextmanager
try:
    from shlex import quote
except ImportError:
    from pipes import quote
import urlparse

from tempest import config
from tempest.lib.common import ssh
from tempest.lib import exceptions as lib_exc


CONF = config.CONF


class SSHClient(object):
    """A client to execute remote commands, based on tempest.lib.common.ssh."""
    _prefix_command = '/bin/bash -c'

    def __init__(self):
        # TODO(stephenfin): Workaround for oslo.config bug #1735790. Remove
        # when oslo.config properly validates required opts registered after
        # basic initialization
        for opt in ['target_private_key_path', 'target_ssh_user',
                    'target_controller']:
            if getattr(CONF.whitebox, opt) is None:
                msg = 'You must configure whitebox.%s' % opt
                raise lib_exc.InvalidConfiguration(msg)

        self.ssh_key = CONF.whitebox.target_private_key_path
        self.ssh_user = CONF.whitebox.target_ssh_user

    def execute(self, hostname=None, cmd=None):
        ssh_client = ssh.Client(hostname, self.ssh_user,
                                key_filename=self.ssh_key)
        cmd = self._prefix_command + ' ' + quote(cmd)
        return ssh_client.exec_command(cmd)

    @contextmanager
    def prefix_command(self, prefix_command=None):
        saved_prefix = self._prefix_command
        self._prefix_command = prefix_command
        yield self
        self._prefix_command = saved_prefix

    @contextmanager
    def sudo_command(self, user=None):
        if user is not None:
            user_arg = '-u {}'.format(user)
        else:
            user_arg = ''
        cmd = 'sudo {} /bin/bash -c'.format(user_arg)
        with self.prefix_command(cmd) as p:
            yield p

    @contextmanager
    def container_command(self, container_name, user=None):
        if user is not None:
            user_arg = '-u {}'.format(user)
        else:
            user_arg = ''
        cmd = 'sudo docker exec {} -i {} /bin/bash -c'.format(
              user_arg, container_name)
        with self.prefix_command(cmd) as p:
            yield p


class VirshXMLClient(SSHClient):
    def __init__(self, hostname=None):
        super(VirshXMLClient, self).__init__()
        self.host = hostname

    def dumpxml(self, domain):
        if CONF.whitebox.containers:
            ctx = self.container_command('nova_compute', user='root')
        else:
            ctx = self.sudo_command()
        with ctx:
            command = "virsh dumpxml {}".format(domain)
            return self.execute(self.host, command)


class MySQLClient(SSHClient):
    def __init__(self):
        super(MySQLClient, self).__init__()
        # the nova conf file may contain a private IP.
        # let's just assume the db is available on the same node.
        self.host = CONF.whitebox.target_controller

        # discover db connection params by accessing nova.conf remotely
        ssh_client = SSHClient()
        if CONF.whitebox.containers:
            ctx = ssh_client.container_command('nova_api')
        else:
            ctx = ssh_client.sudo_command()
        with ctx:
            cmd = """python -c "import nova.conf;
nova.conf.CONF(['--config-file', '/etc/nova/nova.conf']);
print(nova.conf.CONF.database.connection)"
"""
            connection = ssh_client.execute(self.host, cmd).strip()
        p = urlparse.urlparse(connection)

        self.username = p.username
        self.password = p.password
        self.database_host = p.hostname
        self.database = p.path.lstrip('/')
        # if using a deployment with cells, we can safely assume that there's
        # only a single cell. nova_cell0 is a special "cell" database used by
        # the super-conductor so we select the first real cell
        # Refer to https://docs.openstack.org/nova/latest/user/cells.html for
        # more information.
        if self.database == 'nova_cell0':
            self.database = 'nova_cell1'

    def execute_command(self, command):
        sql_cmd = "mysql -u{} -p{} -h{} -e '{}' {}".format(
            self.username,
            self.password,
            self.database_host,
            command,
            self.database)
        return self.execute(self.host, sql_cmd)


class NovaManageClient(SSHClient):
    def __init__(self):
        super(NovaManageClient, self).__init__()
        self.hostname = CONF.whitebox.target_controller

    def execute_command(self, command):
        if CONF.whitebox.containers:
            ctx = self.container_command('nova_api')
        else:
            ctx = self.sudo_command()
        with ctx:
            nova_cmd = "nova-manage {}".format(command)
            return self.execute(self.hostname, nova_cmd)
