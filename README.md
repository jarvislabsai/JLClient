# JLclient

Interacting with Jarvislabs.ai for creating GPU/CPU powered instances on top of A100, A6000, RTX 5000 and RTX6000Ada.

## Installation

```shell



pip install git+https://github.com/jarvislabsai/jlclient.git



```

### Imports and configure

```python



from jlclient import jarvisclient

from jlclient.jarvisclient import *



jarvisclient.token = '**************************duWRbO68IiMTkQKWi48'



```

Generate a token from [here](https://cloud.jarvislabs.ai/settings#api).

## Managing GPU/CPU powered instances on Jarvislabs.ai

### Create

| Parameter           | Type | Description/Values                                                        | Default Value |
| ------------------- | ---- | ------------------------------------------------------------------------- | ------------- |
| instance_type       | str  | Choose between GPU or CPU.                                                | GPU           |
| num_gpus / num_cpus | int  | According to the instance_type, choose the number of GPUs or CPUs.        | 1             |
| gpu_type            | str  | Choose from **A100**, **A5000**, **A6000**, **RTX6000Ada**, **RTX5000**.  | RTX5000       |
| template            | str  | Use `User.get_templates()` in our JLclient to get all templates.          | pytorch       |
| script_id           | str  | If you have a script you can pass it.                                     | None          |
| is_reserved         | bool | True refers to an on-demand instance. False refers to a spot instance.    | True          |
| duration            | str  | Choose hour, week, and month. The pricing changes based on the duration.. | hour          |
| http_ports          | str  | As per your requirement, you can specify the ports.                       | None          |
| hdd                 | int  | Choose between 20GB to 2TB.                                               | 20            |

```python
# CPU Instance Example

instance: Instance = Instance.create('CPU',
                            num_cpus=1,
                            hdd=25,
                            template='pytorch',
                            name='cpu instance')


# GPU Instance Example

instance: Instance = Instance.create('GPU',
                            gpu_type='RTX6000Ada',
                            num_gpus=1,
                            hdd=50,
                            template='pytorch',
                            name='gpu instance')


```

This should return the Instance object, which includes the following attributes

- gpu_type
- num_gpus
- num_cpus
- hdd
- name
- machine_id
- script_id
- is_reserved
- duration
- script_args
- http_ports
- template
- url
- endpoints
- ssh_str
- status

If the Instance object isn't returned, an error dictionary will be provided.

**Note:** Please contact us if you encounter any errors while launching the instance.

### Pause

```python

instance.pause()

```

You can call `pause()` on any `Instance` object.

### Resume

```python

#Example 1:

instance.resume()



#Example 2:

instance.resume(num_gpus=1,

                gpu_type='RTX5000',

                hdd=100)


#Switching GPU to CPU Instance

instance.resume(num_cpus=1,
                hdd=25)

#Switching CPU to GPU Instance

instance.resume(gpu_type='RTX6000Ada',
                num_gpus=1,
                hdd=25)
```

You can modify an existing instance by changing the below `resume` parameters.

- num_gpus

- gpu_type

- hdd

or just call `resume` to start with the same configuration.

### Destroy

```python

instance.destroy()

```

Invoking the destroy method on any instance object will permanently delete the instance and it cannot be retrieve.

## User management.

The `User` class comes with the below key functionalities.

- `User.get_templates()` : Returns the list of templates.

- `User.get_instances()` : Returns a list of `Instance` objects representing instances in your account.

- `User.get_instance()` : Returns the `Instance` Class.

- `User.get_balance()` : Return the balance of the user.

## Issues/Feature request

Do you like to see any new features, we are all ears. You can drop us an email to hello@jarvislabs.ai or chat with us for any new features or issues.

## License

This project is licensed under the terms of the MIT license.
