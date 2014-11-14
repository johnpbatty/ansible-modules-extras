#!/usr/bin/python
#coding: utf-8 -*-

# (c) 2013, Benno Joy <benno@ansible.com>
# (c) 2014, John Batty <john.batty@metaswitch.com>
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.

try:
    try:
        from neutronclient.neutron import client
    except ImportError:
        from quantumclient.quantum import client
    from keystoneclient.v2_0 import client as ksclient
except ImportError:
    print("failed=True msg='quantumclient (or neutronclient) and keystone client are required'")

DOCUMENTATION = '''
---
module: quantum_port
version_added: 
short_description: Adds/removes network ports from OpenStack
description:
  - Add or remove network port from OpenStack.
options:
  login_username:
    description:
      - login username to authenticate to keystone
    required: true
    default: admin
  login_password:
    description:
      - Password of login user
    required: true
    default: 'yes'
  login_tenant_name:
    description:
      - The tenant name of the login user
    required: true
    default: 'yes'
  tenant_name:
    description:
      - The name of the tenant for whom the port is created
    required: false
    default: None
  auth_url:
    description:
      - The keystone url for authentication
    required: false
    default: 'http://127.0.0.1:35357/v2.0/'
  state:
    description:
      - Indicate desired state of the resource
    choices: ['present', 'absent']
    default: present
  name:
    description:
      - Name to be assigned to the port
     required: true
     default: None
  network_name:
    description:
      - Name of the network that the port is on
    required: true
    default: None
  fixed_ip:
    description:
      - A fixed IP address to assign to the port
    required: false
    default: None
  allowed_ip_addrs:
    description:
      - List of additional IP addresses to be allowed on the port

requirements: ["quantumclient", "neutronclient", "keystoneclient"]
'''

EXAMPLES = '''
# Create a port on a named network, with OpenStack allocating the IP address
- quantum_port: name=mgmt_port tenant_name=tenant1 state=present
                network_name=management
                login_username=admin login_password=admin login_tenant_name=admin

# Create a port with a specified IP address.  The IP address must be valid for the
# subnet associated with the named network, and unused by any other ports.
- quantum_port: name=mgmt_port state=present
                network_name=management fixed_ip=192.168.1.10
                login_username=admin login_password=admin login_tenant_name=admin
'''

_os_keystone = None
_os_tenant_id = None
_os_network_id = None
_os_subnet_id = None

def _get_ksclient(module, kwargs):
    try:
        kclient = ksclient.Client(username=kwargs.get('login_username'),
                                 password=kwargs.get('login_password'),
                                 tenant_name=kwargs.get('login_tenant_name'),
                                 auth_url=kwargs.get('auth_url'))
    except Exception, e:
        module.fail_json(msg = "Error authenticating to the keystone: %s" %e.message)
    global _os_keystone
    _os_keystone = kclient
    return kclient

def _get_endpoint(module, ksclient):
    try:
        endpoint = ksclient.service_catalog.url_for(service_type='network', endpoint_type='publicURL')
    except Exception, e:
        module.fail_json(msg = "Error getting network endpoint: %s " %e.message)
    return endpoint

def _get_neutron_client(module, kwargs):
    _ksclient = _get_ksclient(module, kwargs)
    token = _ksclient.auth_token
    endpoint = _get_endpoint(module, _ksclient)
    kwargs = {
        'token': token,
        'endpoint_url': endpoint
    }
    try:
        neutron = client.Client('2.0', **kwargs)
    except Exception, e:
        module.fail_json(msg = " Error in connecting to neutron: %s " %e.message)
    return neutron

def _set_tenant_id(module):
    global _os_tenant_id
    if not module.params['tenant_name']:
        tenant_name = module.params['login_tenant_name']
    else:
        tenant_name = module.params['tenant_name']

    for tenant in _os_keystone.tenants.list():
        if tenant.name == tenant_name:
            _os_tenant_id = tenant.id
            break

    if not _os_tenant_id:
        module.fail_json(msg = "The tenant id cannot be found, please check the parameters")

def _set_network_id(module, neutron):
    global _os_network_id
    kwargs = {
        #'tenant_id': _os_tenant_id,
        'name': module.params['network_name'],
    }
    try:
        networks = neutron.list_networks(**kwargs)
    except Exception, e:
        module.fail_json("Error in listing neutron networks: %s" % e.message)

    if (not networks['networks']) or (len(networks['networks']) < 1):
        module.fail_json(msg = "The network cannot be found: %s" % module.params['network_name'])

    network = networks['networks'][0]

    if not network['id']:
        module.fail_json(msg = "The network id cannot be found: %s" % module.params['network_name'])

    if (not network['subnets']) or (len(network['subnets']) < 1):
        module.fail_json(msg = "The network subnet cannot be found: %s" % module.params['network_name'])

    _os_network_id = network['id']
    _os_subnet_id = network['subnets'][0]

def _get_port(module, neutron):
    kwargs = {
        'tenant_id': _os_tenant_id,
        'name': module.params['name'],
    }
    try:
        ports = neutron.list_ports(**kwargs)
    except Exception, e:
        module.fail_json(msg = "Error in listing neutron ports: %s" % e.message)

    if not ports['ports']:
        return None

    return ports['ports'][0]

def _get_port_id(module, neutron):
    port = _get_port(module, neutron)
    if port:
        return port['id']
    return None

def _create_port(module, neutron):
    neutron.format = 'json'

    port = {
        'name':            module.params.get('name'),
        'tenant_id':       _os_tenant_id,
        'network_id':      _os_network_id,
        'admin_state_up':  module.params.get('admin_state_up'),
    }

    fixed_ip = module.params.get('fixed_ip')
    if fixed_ip:
        port['fixed_ips'] = [{
            'ip_address': fixed_ip,
            'subnet_id':  _os_subnet_id,
        }]

    allowed_ip_addrs = module.params.get('allowed_ip_addrs')
    if allowed_ip_addrs:
        port['allowed_address_pairs'] = [
            { 'ip_address': ip_address } for ip_address in allowed_ip_addrs
        ]

    try:
        port = neutron.create_port({'port': port})
    except Exception, e:
        module.fail_json(msg = "Error in creating port: %s" % e.message)
    return port['port']

def _delete_port(module, neutron, port_id):
    try:
        id = neutron.delete_port(port_id)
    except Exception, e:
        module.fail_json(msg = "Error in deleting the port: %s" % e.message)
    return True

def main():
    argument_spec = openstack_argument_spec()
    argument_spec.update(dict(
        name                            = dict(required=True),
        network_name                    = dict(required=True),
        tenant_name                     = dict(default=None),
        fixed_ip                        = dict(default=None),
        allowed_ip_addrs                = dict(default=None),
        admin_state_up                  = dict(default=True, type='bool'),
        state                           = dict(default='present', choices=['absent', 'present'])
    ))
    module = AnsibleModule(argument_spec=argument_spec)

    neutron = _get_neutron_client(module, module.params)

    _set_tenant_id(module)
    _set_network_id(module, neutron)

    if module.params['state'] == 'present':
        port = _get_port(module, neutron)
        if not port:
            port = _create_port(module, neutron)
            module.exit_json(changed = True, result = "Created", port = port)
        else:
            module.exit_json(changed = False, result = "Success", port = port)

    if module.params['state'] == 'absent':
        port_id = _get_port_id(module, neutron)
        if not port_id:
            module.exit_json(changed = False, result = "Success")
        else:
            _delete_port(module, neutron, port_id)
            module.exit_json(changed = True, result = "Deleted")

# this is magic, see lib/ansible/module_common.py
from ansible.module_utils.basic import *
from ansible.module_utils.openstack import *
main()

