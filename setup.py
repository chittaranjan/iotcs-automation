from setuptools import setup


setup(
    name='iotcs-automation',
    version='1.0',
    py_modules=['iotcs_automation'],
    install_requires=[
        'Click',
        'paramiko',
        'requests'
    ],
    entry_points='''
        [console_scripts]
        iotcsautomation=iotcs_automation:cli
    '''
)