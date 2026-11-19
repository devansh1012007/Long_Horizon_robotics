from setuptools import find_packages, setup

package_name = 'rg_perception'

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
    description='scene_observer and state_estimator nodes (spec Section 7)',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'scene_observer = rg_perception.scene_observer_node:main',
            'state_estimator = rg_perception.state_estimator_node:main',
        ],
    },
)
