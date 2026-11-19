from setuptools import find_packages, setup

package_name = 'rg_task'

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
    description='task_parser and task_planner nodes (spec Section 7).',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'task_parser = rg_task.task_parser_node:main',
            'task_planner = rg_task.task_planner_node:main',
        ],
    },
)
