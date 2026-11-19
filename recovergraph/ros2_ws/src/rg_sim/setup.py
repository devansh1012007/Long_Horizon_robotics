from setuptools import find_packages, setup

package_name = 'rg_sim'

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
    description='kinematic sim bridge and fault injector (MuJoCo in Phase 0/1)',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'sim_bridge = rg_sim.sim_bridge_node:main',
            'fault_injector = rg_sim.fault_injector_node:main',
        ],
    },
)
