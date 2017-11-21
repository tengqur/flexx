"""
Implementation of LocalComponent and ProxyComponent classes, which form
the basis for the PyComponent and JsComponent classes (and their proxies).
"""

import sys
import json
import threading

from ..pyscript import window, JSString, undefined, this_is_js

from .. import event

from ..event import Component, loop, Dict
from ..event._component import (with_metaclass, ComponentMeta)

from ..event._property import Property
from ..event._emitter import EmitterDescriptor
from ..event._action import ActionDescriptor
from ..event._reaction import ReactionDescriptor
from ..event._js import create_js_component_class

from ._asset import get_mod_name
from . import logger


# The clientcore module is a PyScript module that forms the core of the
# client-side of Flexx. We import the serializer instance, and can use
# that name in both Python and JS. Of course, in JS it's just the
# corresponding instance from the module that's being used.
# By using something from clientcore in JS here, we make clientcore a
# dependency of the the current module.
from ._clientcore import serializer, bsdf

manager = None  # Set by __init__ to prevent circular dependencies


def make_proxy_action(action):
    # Note: the flx_prefixes are picked up by the code in flexx.event that
    # compiles component classes, so it can fix /insert the name for JS.
    flx_name = action._name
    def flx_proxy_action(self, *args):
        self._proxy_action(flx_name, *args)
    flx_proxy_action.__doc__ = action.__doc__  # todo: or action._doc?
    return flx_proxy_action  # ActionDescriptor(flx_proxy_action, flx_name, '')


def get_component_classes():
    """ Get a list of all known PyComponent and JsComponent subclasses.
    """
    return [c for c in PyComponentMeta.CLASSES]


def meta_repr(cls):
    """ A repr function to provide some context on the purpose of a class.
    """
    if issubclass(cls, PyComponent):
        prefix = 'PyComponent class'
    elif issubclass(cls, PyComponent.JS):
        prefix = 'proxy PyComponent class for JS '
    elif issubclass(cls, JsComponent):
        prefix = 'proxy JsComponent class'
    elif issubclass(cls, JsComponent.JS):
        prefix = 'JsComponent class for JS'
    else:
        prefix = 'class'
    return "<%s '%s.%s'>" % (prefix, cls.__module__, cls.__name__)


class ComponentMetaJS(ComponentMeta):
    """ Meta class for autogenerated classes intended for JavaScript:
    Proxy PyComponent and local JsComponents.
    """
    
    __repr__ = meta_repr
    
    def __init__(self, name, *args):
        name = name.encode() if sys.version_info[0] == 2 else name
        return super().__init__(name, *args)


class AppComponentMeta(ComponentMeta):
    """ Meta class for PyComponent and JsComponent
    that generate a matching class for JS.
    """
    
    # Keep track of all subclasses
    CLASSES = []
    
    __repr__ = meta_repr
    
    def _init_hook(cls, cls_name, bases, dct):
        
        # Create corresponding class for JS
        if issubclass(cls, LocalComponent):
            cls._make_js_proxy_class(cls_name, bases, dct)
        elif issubclass(cls, ProxyComponent):
            cls._make_js_local_class(cls_name, bases, dct)
        else:  # pragma: no cover
            raise TypeError('Expected class to inherit fro LocalComponent or ProxyComponent.')
        
        # Write __jsmodule__; an optimization for our module/asset system
        cls.__jsmodule__ = get_mod_name(sys.modules[cls.__module__])
        
        # Set JS.CODE and CSS
        cls.JS.CODE = cls._get_js()
        cls.CSS = cls.__dict__.get('CSS', '')
        
        # Register this class. The classes in this list will be automatically
        # "pushed to JS" in a JIT fashion. We have to make sure that we include
        # the code for base classes not in this list, which we do in _get_js().
        AppComponentMeta.CLASSES.append(cls)
    
    def _make_js_proxy_class(cls, cls_name, bases, dct):
        
        for c in bases:
           assert not issubclass(cls, ProxyComponent)
        
        # Fix inheritance for JS variant
        jsbases = [getattr(b, 'JS') for b in cls.__bases__ if hasattr(b, 'JS')]
        if not jsbases:
            jsbases.append(ProxyComponent)
        jsdict = {}
        
        # Copy properties from this class to the JS proxy class.
        # in Python 3.6 we iterate in the order in which the items are defined,
        for name, val in cls.__dict__.items():
            if name.startswith('__') and name.endswith('__'):
                continue
            elif isinstance(val, Property):
                jsdict[name] = val  # properties are the same
            elif isinstance(val, EmitterDescriptor):
                pass  # no emitters on the proxy side
            elif isinstance(val, ActionDescriptor):
                 jsdict[name] = make_proxy_action(val)  # proxy actions
            else:
                pass  # no reactions/functions/class attributes on the proxy side
        
        # Create JS class
        cls.JS = ComponentMetaJS(cls_name, tuple(jsbases), jsdict)
    
    def _make_js_local_class(cls, cls_name, bases, dct):
        
        for c in bases:
           assert not issubclass(cls, LocalComponent)
        
        # Fix inheritance for JS variant
        jsbases = [getattr(b, 'JS') for b in cls.__bases__ if hasattr(b, 'JS')]
        if not jsbases:
            jsbases.append(LocalComponent)
        jsdict = {}
        
        # Copy properties from this class to the JS proxy class.
        # in Python 3.6 we iterate in the order in which the items are defined,
        for name, val in list(cls.__dict__.items()):
            if name.startswith('__') and name.endswith('__'):
                continue
            elif isinstance(val, Property) or (callable(val) and name.endswith('_validate')):
                jsdict[name] = val  # properties are the same
            elif isinstance(val, EmitterDescriptor):
                pass  # todo: create stub emitters for doc purposes
            elif isinstance(val, ActionDescriptor):
                # JS part gets the proper action, Py side gets a proxy action
                jsdict[name] = val
                setattr(cls, name, make_proxy_action(val))
            else:
                # Move attribute from the Py class to the JS class
                jsdict[name] = val
                delattr(cls, name)
                dct.pop(name, None)  # is this necessary? 
        
        # Create JS class
        cls.JS = ComponentMetaJS(cls_name, tuple(jsbases), jsdict)
    
    def _get_js(cls):
        """ Get source code for this class plus the meta info about the code.
        """
        # Since classes are defined in a module, we can safely name the classes
        # by their plain name. 
        # But flexx.classes.X remains the "official" 
        # namespace, so that things work easlily accross modules, and we can
        # even re-define classes (e.g. in the notebook).
        # todo: actually, we got rid of flexx.classes, does that break notebook interactivity?
        cls_name = cls.__name__
        base_class = cls.JS.mro()[1]
        base_class_name = '%s.prototype' % base_class.__name__
        code = []
        
        # Add this class
        c = create_js_component_class(cls.JS, cls_name, base_class_name)
        meta = c.meta
        code.append(c)
        # code.append(c.replace('var %s =' % cls_name,
        #                       'var %s = flexx.classes.%s =' % (cls_name, cls_name), 1))
        
        # Add JS version of Component when this is the Component class
        if base_class is LocalComponent:
            c = create_js_component_class(LocalComponent, 'LocalComponent', 'Component.prototype')
            # c = c.replace('var LocalComponent =',
            #               'var LocalComponent = flexx.classes.LocalComponent =', 1)
            # code.insert(0, 'flexx.classes.Component = Component;')
            code.insert(0, c)
            
        elif base_class is ProxyComponent:
            c = create_js_component_class(ProxyComponent, 'ProxyComponent', 'Component.prototype')
            for k in ['vars_unknown', 'vars_global', 'std_functions', 'std_methods']:
                meta[k].update(c.meta[k])
            # c = c.replace('var ProxyComponent =',
            #               'var ProxyComponent = flexx.classes.ProxyComponent =', 1)
            code.insert(0, c)
        
        # Return with meta info
        js = JSString('\n'.join(code))
        js.meta = meta
        return js


class LocalComponent(Component):
    """
    Base class for PyComponent in Python and JsComponent in JavaScript.
    """
    
    def _comp_init_property_values(self, property_values):
        
        # This is a good time to register with the session, and
        # instantiate the proxy class. Property values have been set at this
        # point, but init() has not yet been called.
        
        self._sessions = []
        
        # Keep track of what events are registered at the proxy
        self.__event_types_at_proxy = {}  # session_id -> event_types
        
        # Pop special attribute
        property_values.pop('flx_is_app', None)
        # Pop and apply id if given
        id = property_values.pop('flx_id', None)
        if id:
            self._id = id
        # Pop session
        session = property_values.pop('flx_session', None)
        if session is not None:
            self._sessions.append(session)
        
        # Call original method
        prop_events = super()._comp_init_property_values(property_values)
        
        if this_is_js():
            # This is a proxy PyComponent in JavaScript
            pass
        
        else:
            # This is a proxy JsComponent in Python
            # A local component can be associated with multiple sessions
            
            # Register this component with the session. Sets the id.
            # todo: uid + global count
            for session in self._sessions:
                session._register_component(self)
            
            # Instantiate JavaScript version of this class
            # todo: how to deal with 0 or more sessions?
            for session in self._sessions:
                session.send_command('INSTANTIATE', self.__jsmodule__,
                                     self.__class__.__name__,
                                     self._id, [], {})
        
        return prop_events
        
        # todo: ? self._session.keep_alive(self)
    
    def _set_event_types(self, session_id, event_types):
        self.__event_types_at_proxy[session_id] = event_types
    
    def emit(self, type, info=None):
        # Overload emit() so we can send events to the proxy object at the other end
        ev = super().emit(type, info)
        # todo: do we want a way to keep props local? Or should one just wrap or use a second class?
        isprop = type in self.__properties__
        if not self._disposed:
            for session in self._sessions:
                 if isprop or type in self.__event_types_at_proxy.get(session.id, []):
                    session.send_command('INVOKE', self._id, '_emit_from_other_side', [ev])
    
    # todo: probably remove this, we have actions now!
    # def call_js(self, call):
    #     if self._disposed:
    #         return
    #     if not this_is_js():
    #         # todo: Not documented; not sure if we keep it. Handy for debugging though
    #         for session in self._sessions:
    #             cmd = 'flexx.sessions["%s"].get_instance("%s").%s;' % (
    #                     session.id, self._id, call)
    #             session._exec(cmd)


class ProxyComponent(Component):
    """
    Base class for JSComponent in Python and PyComponent in JavaScript.
    
    We keep a pool of these, and only really remove when disposed from JS, or
    when the session closes. Or not? What if a JS component is deleted without
    having been disposed?
    """
    
    def __init__(self, *init_args, **kwargs):
        # Need to overload __init__() to handle init_args
        
        if this_is_js():
            # This is a proxy PyComponent in JavaScript.
            # Always instantiated via a command from Python.
            # todo: not true, can also be referenced later, I guess?
            assert len(init_args) == 0
            super().__init__(**kwargs)
        else:
            # This is a proxy JsComponent in Python.
            # Can be instantiated in Python, 
            self._init_args = init_args
            super().__init__(**kwargs)
    
    def _comp_init_property_values(self, property_values):
        
        # This is a good time to register with the session, and
        # instantiate the proxy class. Property values have been set at this
        # point, but init() has not yet been called.
        
        self._sessions = []
        
        # Pop special attribute
        property_values.pop('flx_is_app', None)
        # Pop and apply id if given
        id = property_values.pop('flx_id', None)
        if id:
            self._id = id
        # Pop session
        session = property_values.pop('flx_session', None)
        if session is not None:
            self._sessions.append(session)
        
        prop_events = super()._comp_init_property_values(property_values)
        
        if this_is_js():
            # This is a proxy PyComponent in JavaScript
            pass
        
        else:
            # This is a proxy JsComponent in Python
            # A local component can be associated with multiple sessions
            
            # Register this component with the session. Sets the id.
            # todo: uid + global count
            for session in self._sessions:
                session._register_component(self)
            
            # Instantiate JavaScript version of this class
            # todo: can be exacyly one session!
            assert len(self._sessions) == 1
            for session in self._sessions:
                session.send_command('INSTANTIATE', self.__jsmodule__,
                                     self.__class__.__name__,
                                     self._id, self._init_args, {})
                del self._init_args
        
        return prop_events
    
    def _proxy_action(self, name, *args, **kwargs):
        """ To invoke actions on the real object.
        """
        assert not kwargs
        for session in self._sessions:
            session.send_command('INVOKE', self._id, name, args)
    
    def _mutate(self, *args, **kwargs):
        """ Disable mutations on the proxy class.
        """
        raise RuntimeError('Cannot mutate properties from a proxy class.')
        # Reference objects to get them collected into the JS variant of this
        # module. Do it here, in a place where it wont hurt.
        serializer  # to bring in _clientcore as a way of bootstrapping
        BsdfComponentExtension
    
    def _registered_reactions_hook(self):
        """ Keep the local component informed about what event types this proxy
        is interested in. This way, the trafic can be minimized, e.g. not send
        mouse move events if they're not used anyway.
        """
        event_types = super()._registered_reactions_hook()
        try:
            if not self._disposed:
                for session in self._sessions:
                    session.send_command('INVOKE', self._id, '_set_event_types', [session.id, event_types])
        finally:
            return event_types
    
    @event.action
    def _emit_from_other_side(self, ev):
        """ Action used by the local component to push an event to the proxy
        component. If the event represents a property-update, the mutation
        is applied, otherwise the event is emitted here.
        """
        if not this_is_js():
            ev = Dict(ev)
        if ev.type in self.__properties__ and hasattr(ev, 'mutation'):
            # Mutate the property - this will cause an emit
            if ev.mutation == 'set':
                super()._mutate(ev.type, ev.new_value)
            else:
                super()._mutate(ev.type, ev.objects, ev.mutation, ev.index)
        else:
            self.emit(ev.type, ev)


# LocalComponent and ProxyComponent need __jsmodule__, but they do not
# participate in the AppComponentMeta class, so we add it here.
LocalComponent.__jsmodule__ = ProxyComponent.__jsmodule__ = __name__


class PyComponent(with_metaclass(AppComponentMeta, LocalComponent)):
    """ Base component class that operates in Python, but is accessible
    in JavaScript, where its properties and events can be observed,
    and actions can be invoked.
    """
    
    # the meta class will generate a PyComponent proxy class for JS
    
    def __repr__(self):
        return "<PyComponent '%s' at 0x%x>" % (self._id, id(self))


class JsComponent(with_metaclass(AppComponentMeta, ProxyComponent)):
    """ Base component class that operates in JavaScript, but is accessible
    in Python, where its properties and events can be observed,
    and actions can be invoked.
    """
    
    # the meta class will generate a JsComponent local class for JS
    # and move all props, actions, etc. to it
    
    def __repr__(self):
        return "<JsComponent '%s' at 0x%x>" % (self._id, id(self))


class BsdfComponentExtension(bsdf.Extension):
    
    name = 'flexx.app.component'
    cls = PyComponent, JsComponent
    
    def match(self, c):
        # This is actually the default behavior, but added for completenes
        return isinstance(c, self.cls)
    
    def encode(self, c):
        assert len(c._sessions) == 1
        return dict(session_id=c._sessions[0].id, id=c._id)
    
    def decode(self, d):
        try:
            session = manager.get_session_by_id(d['session_id'])
            return session.get_component_instance_by_id(d['id'])
        except Exception:
            return d.get('id', 'unknown_component')
    
    # The name and below methods get collected to produce a JS BSDF extension
    
    def match_js(self, c):
        return isinstance(c, PyComponent) or isinstance(c, JsComponent)
        
    def encode_js(self, c):
        assert len(c._sessions) == 1
        return dict(session_id=c._sessions[0].id, id=c._id)
    
    def decode_js(self, d):
        session = window.flexx.sessions[d['session_id']]
        if session:
            c = session.get_instance(d['id'])
            if c:
                return c
        return 'unknown_component'


# todo: can the mechanism for defining BSDF extensions be simplified?
# Add BSDF extension for serializing components. The JS variant of the
# serializer is added by referencing the extension is JS code.
serializer.add_extension(BsdfComponentExtension)
