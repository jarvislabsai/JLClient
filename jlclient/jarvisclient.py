from .httpclient import post, get, get_base_url
from jlclient import jarvisclient
import time
token = None
DEFAULT_REGION = 'india-01'
EUROPE_REGION = 'europe-01'
EUROPE_GPU_TYPES = {'H100', 'H200'}
EUROPE_GPU_COUNTS = {1, 8}
EUROPE_MIN_STORAGE_GB = 100


def _default_region_for_gpu(gpu_type):
    return EUROPE_REGION if gpu_type in EUROPE_GPU_TYPES else DEFAULT_REGION


def _resolve_region(instance_type, gpu_type, num_gpus):
    is_cpu_request = (instance_type or '').lower() == 'cpu'
    fallback_region = DEFAULT_REGION if is_cpu_request else _default_region_for_gpu(gpu_type)

    try:
        meta = get('misc/server_meta', jarvisclient.token)
    except Exception:
        return fallback_region

    if is_cpu_request:
        region = meta.get('cpu_meta', {}).get('region')
        return region or DEFAULT_REGION

    candidates = [
        server for server in meta.get('server_meta', [])
        if server.get('gpu_type') == gpu_type and server.get('region')
    ]
    if not candidates:
        return fallback_region

    required = int(num_gpus or 1)
    for server in candidates:
        if int(server.get('num_free_devices') or 0) >= required:
            return server.get('region')

    return candidates[0].get('region') or fallback_region


def _validate_europe_nebius_request(region, gpu_type, num_gpus, storage):
    # Keep client-side guardrails aligned with Europe (Nebius) backend expectations.
    if region != EUROPE_REGION:
        return

    if gpu_type not in EUROPE_GPU_TYPES:
        raise ValueError("europe-01 supports only H100/H200 GPU requests.")

    if num_gpus not in EUROPE_GPU_COUNTS:
        raise ValueError("For europe-01, num_gpus must be 1 or 8 for H100/H200.")

    if storage < EUROPE_MIN_STORAGE_GB:
        raise ValueError("For europe-01, storage must be at least 100 GB.")


def _validate_template_region_request(template, instance_type, gpu_type, region):
    if template != 'vm':
        return

    if (instance_type or '').lower() != 'gpu':
        raise ValueError("template='vm' supports only GPU instances.")

    if gpu_type not in EUROPE_GPU_TYPES:
        raise ValueError("template='vm' supports only H100/H200.")

    if region and region != EUROPE_REGION:
        raise ValueError("template='vm' is supported only in europe-01.")


def _extract_error_message(resp):
    if isinstance(resp, dict):
        return resp.get('detail') or resp.get('message') or resp.get('error') or str(resp)
    return str(resp)

class Instance(object):
    def __init__(self,
                 hdd: int,
                 gpu_type: str,
                 machine_id: int,
                 num_gpus: int = None,
                 num_cpus: int = None,
                 name: str = '',
                 script_id: str = '',
                 is_reserved: bool = True,
                 url: str = '',
                 status: str = '',
                 ssh_str: str = '',
                 endpoints: str = '',
                 duration: str = 'hour',
                 script_args: str = '',
                 http_ports: str = '',
                 template: str = ''
                 ):
        
        self.gpu_type = gpu_type
        self.num_gpus = num_gpus
        self.num_cpus = num_cpus
        self.hdd = hdd
        self.name = name
        self.machine_id = machine_id
        self.script_id = script_id
        self.is_reserved = is_reserved
        self.duration = duration
        self.script_args = script_args
        self.http_ports = http_ports
        self.template = template
        self.url = url
        self.endpoints = endpoints
        self.ssh_str = ssh_str
        self.status = status
        self.region = DEFAULT_REGION

    def pause(self):
        '''
        Pause the running machine.
        Returns:
            status: Returns the pause status of the machine --> success or failed.
        '''
        # UI parity: VM uses template route, everything else uses misc route.
        endpoint = 'templates/vm/pause' if self.template == 'vm' else 'misc/pause'
        pause_response = post({}, endpoint, 
                              jarvisclient.token,
                              query_params={'machine_id':f'{self.machine_id}'},
                              base_url=get_base_url(self.region))
        if pause_response.get('success'):
            self.status = 'Paused'
        return pause_response
    
    def destroy(self):
        '''
        Destroy the running or paused machine. 
        Returns:
            status:  Returns the destroy status of the machine --> success or failed.
        '''
        # UI parity: VM uses template route, everything else uses misc route.
        endpoint = 'templates/vm/destroy' if self.template == 'vm' else 'misc/destroy'
        destroy_response = post({},
                                endpoint,
                                jarvisclient.token,
                                query_params={'machine_id': self.machine_id},
                                base_url=get_base_url(self.region))
        if destroy_response.get('success'):
            self.status = 'Destroyed'
        return destroy_response
    
    def update_instance_meta(self,req,machine_details):
        self.machine_id = machine_details.get('machine_id')
        self.gpu_type = req.get('gpu_type')
        self.num_gpus = req.get('num_gpus')
        self.hdd = int(req.get('hdd'))
        self.is_reserved = req.get('is_reserved')
        self.name = req.get('name')
        self.num_cpus = req.get('num_cpus')
        self.url = machine_details.get('url')
        self.endpoints = machine_details.get('endpoints')
        self.ssh_str = machine_details.get('ssh_str')
        self.status = machine_details.get('status')
        self.machine_id=machine_details.get('machine_id')
        self.duration=machine_details.get('frequency')
        self.template=machine_details.get('framework')
        self.region = machine_details.get('region', self.region)

    def resume(self,
               storage: int=None,
               num_cpus: int = None,
               num_gpus :int=None,
               gpu_type: str=None,
               name: str=None,
               script_id: str=None,
               script_args: str=None,
               is_reserved: bool=None,
               duration: str=None,
               fs_id: str=None
               ):
        resume_req = {
            'machine_id': self.machine_id,
            'hdd' :  storage or self.hdd,
            'name' : name or self.name,
            'script_id' :  script_id or self.script_id,
            'script_args' : script_args or self.script_args,
            'duration' : duration or self.duration,
            'gpu_type': None,
            'num_gpus': None,
            'num_cpus': None,
            'fs_id': fs_id
        }

        if num_cpus or self.gpu_type == 'CPU' and not num_gpus:
            resume_req['num_cpus'] = num_cpus if num_cpus else self.num_cpus
        else:
            resume_req['gpu_type'] = gpu_type if gpu_type else self.gpu_type
            resume_req['num_gpus'] = num_gpus if num_gpus else self.num_gpus
            resume_req['is_reserved'] = is_reserved if is_reserved is not None else self.is_reserved
        
        try:
            _validate_europe_nebius_request(region=self.region,
                                            gpu_type=resume_req.get('gpu_type'),
                                            num_gpus=resume_req.get('num_gpus'),
                                            storage=resume_req.get('hdd'))

            resume_resp = post(resume_req,f'templates/{self.template}/resume', jarvisclient.token, base_url=get_base_url(self.region))
            if 'machine_id' not in resume_resp:
                return {'error_message': _extract_error_message(resume_resp)}
            self.machine_id = resume_resp['machine_id']
            machine_details = Instance.get_instance_details(machine_id=self.machine_id, region=self.region)
            self.update_instance_meta(req=resume_req,machine_details=machine_details)
            return self

        except ValueError as e:
            return {'error_message': str(e)}
        
        except InstanceCreationException as e:
            return {'error_message': str(e)}

        except Exception as e:
            return {'error_message' : "Some unexpected error had occured. Please reach to the team."}

    def get_instance_details(machine_id, region=DEFAULT_REGION):
        max_attempts = 18

        for _ in range(max_attempts):
            machine_status_response = get('users/fetch',
                                      jarvisclient.token,
                                      base_url=get_base_url(region))
            
            matching_instances = [instance for instance in machine_status_response['instances'] 
                                if instance.get('machine_id') == machine_id]
            machine_details = matching_instances[0] if matching_instances else None
            if machine_details and machine_details.get('status') == 'Running':
                return machine_details
            if machine_details and machine_details.get('status') == 'Failed':
                raise InstanceCreationException("Instance creation failed.")
            time.sleep(10)

        raise InstanceCreationException(
            f"Timed out while waiting for machine_id={machine_id} to reach Running."
        )

    @classmethod
    def create(cls,
               instance_type :str,
               gpu_type: str = 'RTX5000',
               template: str = 'pytorch', 
               num_gpus: int = 1,
               num_cpus: int = 1,
               storage: int = 20,
               name: str = 'Name me',
               script_id: str = None,
               image: str = None,
               script_args: str = None,
               is_reserved :bool = True,
               duration: str = 'hour',
               http_ports : str = '',
               fs_id: str = None,
               region: str = None
               ):
        resolved_region = region if region else _resolve_region(instance_type=instance_type,
                                                                 gpu_type=gpu_type,
                                                                 num_gpus=num_gpus)
        req_data = {'hdd':storage,
                    'name':name,
                    'script_id':script_id,
                    'image':image,
                    'script_args':script_args,
                    'is_reserved' :is_reserved,
                    'duration':duration,
                    'http_ports' :http_ports,
                    'fs_id':fs_id,
                    'region': resolved_region}
        instance_params = {}
        instance_type = instance_type.lower()

        if instance_type == 'gpu':
            req_data['gpu_type'] = gpu_type
            req_data['num_gpus'] = num_gpus
            instance_params['gpu_type'] = gpu_type
            instance_params['num_gpus'] = num_gpus
        elif instance_type == 'cpu':
            req_data['num_cpus'] = num_cpus
            instance_params['gpu_type'] = 'CPU'
            instance_params['num_cpus'] = num_cpus

        try:
            _validate_template_region_request(template=template,
                                              instance_type=instance_type,
                                              gpu_type=gpu_type,
                                              region=resolved_region)
            if instance_type == 'gpu':
                _validate_europe_nebius_request(region=resolved_region,
                                                gpu_type=gpu_type,
                                                num_gpus=num_gpus,
                                                storage=storage)
            elif resolved_region == EUROPE_REGION:
                raise ValueError("europe-01 supports only H100/H200 GPU requests.")

            resp = post(req_data, f'templates/{template}/create', jarvisclient.token, base_url=get_base_url(resolved_region))
            if 'machine_id' not in resp:
                return {'error_message': _extract_error_message(resp)}
            machine_id = resp['machine_id']
            machine_details = Instance.get_instance_details(machine_id=machine_id, region=resolved_region)
            instance_params.update({
                'hdd': storage,
                'name': machine_details.get('name'),
                'url': machine_details.get('url'),
                'endpoints': machine_details.get('endpoints'),
                'ssh_str': machine_details.get('ssh_str'),
                'status': machine_details.get('status'),
                'machine_id': machine_details.get('machine_id'),
                'duration': machine_details.get('frequency'),
                'template': machine_details.get('framework'),
        })

            instance = cls(**instance_params)
            instance.region = machine_details.get('region', resolved_region)
            return instance

        except ValueError as e:
            return {'error_message': str(e)}
        
        except InstanceCreationException as e:
            return {'error_message': str(e)}

        except Exception as e:
            return {'error_message' : "Some unexpected error had occured. Please reach to the team."}

    def __str__(self):
        """Returns a formatted string with instance metadata when the object is printed."""
        metadata = [
            f"Instance Name: {self.name}",
            f"Status: {self.status}",
            f"Machine ID: {self.machine_id}",
            f"GPU Type: {self.gpu_type}",
            f"Number of GPUs: {self.num_gpus}",
            f"Number of CPUs: {self.num_cpus}",
            f"Storage (GB): {self.hdd}",
            f"Template: {self.template}",
            f"Duration: {self.duration}",
            f"SSH Command: {self.ssh_str}",
            f"URL: {self.url}",
            f"Endpoints: {self.endpoints}"
        ]
        return "\n".join(metadata)

class InstanceCreationException(Exception):
    """Exception raised when instance creation fails."""

    def __init__(self, message="Failed to create the instance. Please check it."):
        self.message = message
        super().__init__(self.message)

class User(object):
    def __init__(self) -> None:
        pass
    
    @classmethod
    def get_instances(cls)->list[Instance]:
        resp = get(f"users/fetch", 
                    jarvisclient.token)
        instances = []
        for instance in resp['instances']:
            inst = Instance(hdd=int(instance['hdd']),
                            gpu_type=instance['gpu_type'],
                            name=instance['instance_name'],
                            url=instance['url'],
                            endpoints=instance['endpoints'],
                            ssh_str=instance['ssh_str'],
                            status=instance['status'],
                            machine_id=instance['machine_id'],
                            # duration=instance['frequency'],
                            template=instance['framework'],
                            num_gpus=instance['num_gpus'],
                            )
            inst.region = instance.get('region', DEFAULT_REGION)
            instances.append(inst)
        return instances
    
    @classmethod
    def get_instance(cls, instance_id=None) -> Instance:
        assert instance_id != None, 'pass a valid instance/machine id'
        instances = User.get_instances()
        instance = [instance for instance in instances if str(instance.__dict__['machine_id']) == str(instance_id)]
        if len(instance) == 1:
            return instance[0]
        print("Invalid machine id")
    
    @classmethod
    def get_templates(cls):
        templates = get(f"misc/frameworks", 
                    jarvisclient.token)
        return {'templates' : [template['id'] for template in templates['frameworks']]}
    
    @classmethod
    def get_balance(cls):
        return get(f"users/balance", 
                    jarvisclient.token)
    
    @classmethod
    def get_scripts(cls):
        resp = get("/scripts/",jarvisclient.token)
        return resp['script_meta']
    
class FileSystem(object):
    def list(self):
        return get(f"filesystem/list",jarvisclient.token)
    
    def create(self, fs_name, storage):
        return post({'fs_name':fs_name,'storage':storage},
                    f"filesystem/create",
                    jarvisclient.token
                    )
    
    def delete(self, fs_id):
        return post({},
                    f"filesystem/delete",
                    jarvisclient.token,
                    query_params={'fs_id':fs_id})
