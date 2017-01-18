# iotcs-automation

iotcsautomation --help

Usage: iotcsautomation [OPTIONS]

  This script creates a git patch of your local iot-cs project.

  Copies and applies the patch on your remote / devops server.

  Builds the iotcs project and deploys the Server-All.ear on the remote /
  devops machine Weblogic AdminServer.

  Drop and create the database tables using dev-install.sql

  Drop the ES indexes

Options:
  --remotehost TEXT       The hostname of the target / remote machine
  --local-proj-dir TEXT   The path to the iotcs project directory on the local machine
  --remote-proj-dir TEXT  The path to the iotcs project directory on the remote machine
  --db-url TEXT           The database connection url, if provided will run drop and create on the db
  --es-url TEXT           The es connection url, if provided will drop all fm and pm indexes
  --help                  Show this message and exit.


### Installation

Currently only supported on Linux machine and Python3

1. cd to the directory where you want to install
2. clone the repo => git clone https://github.com/rahulpaul/iotcs-automation.git
3. create a virtualenv => python3 -m venv iotcs-automation
4. cd to the project directory => cd iotcs-automation
5. activate the virtualenv => source bin/activate
6. upgrade pip => pip install --upgrade pip
7. install the project => pip install --editable .
8. verify everything was installed correctly => pip freeze
    
### "pip freeze" should output something like the below:

cffi==1.9.1
click==6.7
cryptography==1.7.1
idna==2.2
-e git+https://github.com/rahulpaul/iotcs-automation.git@4ab9b29f58b9bd9a635dedf9146337a0b47d477d#egg=iotcs_automation
paramiko==2.1.1
pyasn1==0.1.9
pycparser==2.17
requests==2.12.5
six==1.10.0

