r"""
===============================================================================
:mod:`OpenPNM.Physics` -- Pore Scale Physics Models
===============================================================================

.. module:: OpenPNM.Physics

Contents
--------
This submodule contains all pore scale physics models applied to a pore network.
   
"""

#Import every file in the directory
import os as _os
dir = _os.path.dirname(_os.path.abspath(__file__))
for item in _os.listdir(dir):
    if item.split('.')[-1] == 'py':
        if item == '__init__.py':
            pass
        elif item[0:2] == '__':
            exec('from .' + item.split('.')[0] + ' import ' + item.split('__')[1])
        else:
            exec('from . import ' + format(item.split('.')[0]))
