from setuptools import find_packages, setup

package_name = 'rg_exec'

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
    description='skill_executor and safety_supervisor nodes (spec Section 7)',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'skill_executor = rg_exec.skill_executor_node:main',
            'safety_supervisor = rg_exec.safety_supervisor_node:main',
        ],
    },
)
