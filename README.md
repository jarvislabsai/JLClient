# JLclient
Interacting with Jarvislabs.ai for creating GPU/CPU powered instances on top of A100, A6000, RTX 5000 and RTX6000. 

**Note: JLclient is currently in beta and is invite only. If you are interested to try, please drop an email to hello@jarvislabs.ai**

## Installation


```

pip install git+https://github.com/jarvislabsai/jlclient.git

```

### Imports and configure

```

from jlclient import jarvisclient
from jlclient.jarvisclient import *

jarvisclient.token = '**************************duWRbO68IiMTkQKWi48'
jarvisclient.user_id = '****************@gmail.com'

```

Generate token from the UI. 

## Managing GPU/CPU powered instances on Jarvislabs.ai

### Create

```
instance = Instance.create(gpu_type='A100',
                            num_gpus=1,
                            hdd=20,
                            framework_id=0,
                            name='IamAI',
                            script_id=1)
```

Through instance object you can access the below key attributes.
- url
- tboard_url
- machine_id
- ssh_str
- name
- status

### Pause

```
instance.pause()
```

You can call `pause()` on any `Instance` object. 

### Resume

```
#Example 1:
instance.resume()

#Example 2:
instance.resume(num_gpus=1,
                gpu_type='RTX5000',
                hdd=100)
```

You can modify an existing instance by changing the below `resume` parameters.
- num_gpus
- gpu_type
- hdd

or just call `resume` to start with the same configuration.

### Destroy

```
instance.destroy()
```

## User management.

The `User` class comes with the below key functionalities.

- `User.get_instances()` : Returns a list of `Instance` objects representing instances in your account.
- `User.add_script()`    : You can add upto 3 scripts. It returns a script_id which you can use while creating an instance. The script is automaticall run during instance startup. 
- `User.delete_script()` : Delete any unused script.
- `User.get_script()`    : List all the scripts associated with your user account. 


### Example for adding script

```
User.add_script(script_path='install_fastai.sh',
                script_name='myscript')
```

## Issues/Feature request

Do you like to see any new features, we are all ears. You can drop us an email to hello@jarvislabs.ai or chat with us for any new features or issues. 


## License

This project is licensed under the terms of the MIT license.