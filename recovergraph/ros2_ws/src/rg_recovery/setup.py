from setuptools import find_packages, setup

package_name = 'rg_recovery'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='RecoverGraph',
    maintainer_email='recovergraph@example.com',
    description='effect_verifier, fault_diagnoser, recovery_planner nodes',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'effect_verifier = rg_recovery.effect_verifier_node:main',
            'fault_diagnoser = rg_recovery.fault_diagnoser_node:main',
            'recovery_planner = rg_recovery.recovery_planner_node:main',
        ],
    },
)
