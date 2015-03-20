""" Base class for objects that live in both Python and JS
"""

import sys

if sys.version_info[0] >= 3:
    string_types = str,
else:
    string_types = basestring,


# From six.py
def with_metaclass(meta, *bases):
    """Create a base class with a metaclass."""
    # This requires a bit of explanation: the basic idea is to make a dummy
    # metaclass for one level of class instantiation that replaces itself with
    # the actual metaclass.
    class metaclass(meta):
        def __new__(cls, name, this_bases, d):
            return meta(name, bases, d)
    return type.__new__(metaclass, 'temporary_class', (), {})


class Prop(object):
    """ A prop can be assigned to a class attribute.
    
    To use props, simply assign them as class attributes::
    
        class Foo(HasProps):
            size = Int(3, 'The size of the foo')
            title = Str('nameless', 'The title of the foo')
    
    To create new props, inherit from Prop and implement validate()::
    
        class MyProp(Prop):
            _default = None  # The default for when no default is given
            def validate(self, val):
                assert isinstance(val, int, float)  # require scalars
                return float(val)  # always store as float
    
    Any prop can only store hashable objects. This includes tuples,
    provided that the items it contains are hashable too. A prop may
    accept* nonhashable values (e.g. list), but the data must be stored
    as an immutable object.
    
    Prop objects are attached to classes, and behave like properties
    because they implement the data descriptor protocol (__get__,
    __set__, and __delete__). The actual value is stored on the instance
    of the class that the prop applies to.
    
    """
    _default = None  # The class _default is the default when no default is given
    
    def __init__(self, default=None, help=None):
        # Init name, set the first time that an instance is created
        self._name = None
        # Set default to prop-default if not given and validate
        default = self._default if default is None else default
        self._default = self.validate(default)
        # Set doc
        self.__doc__ = help or 'A %s property' % self.__class__.__name__
    
    def __get__(self, obj, objType=None):
        if obj is not None:
            return self._get(obj)
        elif objType is not None:
            return self
        else:
            raise ValueError("both 'obj' and 'owner' are None, don't know what to do")
    
    def __set__(self, obj, value):
        # Validate value, may return a "cleaned up" version
        value = self.validate(value)
        
        # Set 
        try:
            newhash = hash(value)
        except TypeError:
            raise TypeError('Prop values need to be hashable, %r is not.' % 
                            type(value).__name__)
        
        # If same as old value, early exit
        #old = self._get(obj)
        #if value == old:
        oldhash = getattr(obj, self._private_name + '_hash')
        if newhash == oldhash:
            return
        else:
            setattr(obj, self._private_name + '_hash', newhash)
        
        # Update
        if hasattr(obj, '_changed_props'):
            obj._changed_props.add(self.name)
        setattr(obj, self._private_name, value)
        
        # if hasattr(obj, '_trigger'):
        #     if hasattr(obj, '_block_callbacks') and obj._block_callbacks:
        #         obj._callback_queue.append((self.name, old, value))
        #     else:
        #         obj._trigger(self.name, old, value)

    def __delete__(self, obj):
        if hasattr(obj, self._private_name):
            delattr(obj, self._private_name)
    
    @property
    def name(self):
        return self._name
    
    @property
    def _private_name(self):
        return '_' + self._name
    
    @property
    def default(self):
        return self._default
    
    def _get(self, obj):
        """ Get the value from the object that this is a prop of. """
        # if not hasattr(obj, self._private_name):
        #     setattr(obj, self._private_name, self.default)
        return getattr(obj, self._private_name)
    
    def validate(self, value):
        raise NotImplementedError()


# Note, we need Prop defined before HasProps, because we need to test
# isinstance(cls, Prop) in the meta class (on class creation)


class HasPropsMeta(type):
    """ Meta class for HasProps
    * Sets __props__ attribute on the class
    * Set the name of each prop
    * Initialize value for each prop (to default)
    """
    
    CLASSES = []
    
    def __init__(cls, name, bases, dct):
        
        HasPropsMeta.CLASSES.append(cls)
        
        # Collect props defined on the given class
        props = {}
        for name, prop in dct.items():
            if isinstance(prop, type) and issubclass(prop, Prop):
                prop = prop()
                setattr(cls, name, prop)  # allow setting just class
            if isinstance(prop, Prop):
                props[name] = prop
        # Finalize all found props
        for name, prop in props.items():
            assert prop._name is None
            prop._name = name
            setattr(cls, prop._private_name, prop.default)
            setattr(cls, prop._private_name + '_hash', hash(prop.default))
        # Cache prop names
        cls.__props__ = set(props.keys())
        # Proceeed as normal
        type.__init__(cls, name, bases, dct)


class HasProps(with_metaclass(HasPropsMeta, object)):
    """ A base class for objects that have props.
    
    """
    
    def __init__(self, **kwargs):
        # Variables changed since creation
        self._changed_props = set()
        # Assign 
        for name, val in kwargs.items():
            setattr(self, name, val)
        self.reset_changed_props()
    
    @property
    def changed_props(self):
        """ Get a set of names of props that changed since object creation
        or the last call to reset_changed_props().
        """
        return _changed_props
    
    def reset_changed_props(self):
        self._changed_props = set()
    
    @classmethod
    def props(cls, withbases=True):
        props = set()
        def collect(cls):
            props.update(cls.__props__)
            if withbases:
                for base in cls.__bases__:
                    if hasattr(base, '__props__'):
                        collect(base)
        collect(cls)
        return props
    
    def __setattr__(self, name, value):
        if name.startswith("_"):
            super(HasProps, self).__setattr__(name, value)
        else:
            props = sorted(self.props())
            if name in props:
                super(HasProps, self).__setattr__(name, value)
            else:
                matches, text = props, "possible"
                raise AttributeError("unexpected attribute %r to %s." %
                                    (name, self.__class__.__name__))


## The propery implementations

class Bool(Prop):
    _default = False
    
    def validate(self, val):
        return bool(val)


class Int(Prop):
    _default = 0
    # todo: min, max?
    def validate(self, val):
        if not isinstance(val, (int, float)):
            raise ValueError('Int prop %r requires a scalar.' % self.name)
        return int(val)


class Float(Prop):
    _default = 0.0
    
    def validate(self, val):
        if not isinstance(val, (int, float)):
            raise ValueError('Float prop %r requires a scalar.' % self.name)
        return float(val)


class Str(Prop):
    _default = ''
    
    def validate(self, val):
        if not isinstance(val, str):
            raise ValueError('Str prop %r requires a string.' % self.name)
        return val


class Tuple(Prop):
    _default = ()
    # todo: allowed lengths?
    def __init__(self, item_type, default=None, help=None):
        Prop.__init__(self, default, help)
        item_type = item_type if isinstance(item_type, tuple) else (item_type, )
        self._item_types = item_type
    
    def validate(self, val):
        
        if isinstance(val, (tuple, list)):
            for e in val:
                if not isinstance(e, self._item_types):
                    item_types = ', '.join([t.__name__ for t in self._item_types])
                    this_type = e.__class__.__name__
                    raise ValueError('Tuple prop %r needs items to be in '
                                     '[%s], but got %s.' % 
                                     (self.name, item_types, this_type))
            return val
        else:
            raise ValueError('Tuple prop %r requires tuple or list.' % self.name)


class Color(Prop):
    _default = (1, 1, 1)
    
    # todo: this is a stub. Need HTML names, #rgb syntax etc.
    _color_names = {'red': (1, 0, 0), 'green': (0, 1, 0), 'blue': (0, 0, 1)}
    
    def validate(self, val):
        if isinstance(val, string_types):
            val = val.lower()
            if val in self._color_names:
                return self._color_names[val]
            else:
                raise ValueError('Color prop %r does not understand '
                                 'given string.' % self.name)
        elif isinstance(val, tuple):
            val = tuple([float(v) for v in val])
            if len(val) in (3, 4):
                return val
            else:
                raise ValueError('Color prop %r needs tuples '
                                 'of 3 or 4 elements.' % self.name)
        else:
            raise ValueError('Color prop %r requires str or tuple.' % self.name)


class Instance(Prop):
    _default = None
    
    def __init__(self, item_type, default=None, help=None):
        Prop.__init__(self, default, help)
        item_type = item_type if isinstance(item_type, tuple) else (item_type, )
        self._item_types = item_type
    
    def validate(self, val):
        if val is None:
            return val
        elif isinstance(val, self._item_types):
            return val
        else:
            item_types = ', '.join([t.__name__ for t in self._item_types])
            this_type = val.__class__.__name__
            raise ValueError('Instance prop %r needs items to be in '
                             '[%s], but got %s.' % 
                             (self.name, item_types, this_type))
    
    
## -----
# todo: the above is generic and need to go in utils, below is JS related


def get_mirrored_classes():
    return [c for c in HasPropsMeta.CLASSES if issubclass(c, Mirrored)]


from zoof.ui.compile import js

class Mirrored(HasProps):
    """ Instances of this class will have a mirror object in JS. The
    props of the two are synchronised.
    """
    
    name = Str()
    _counter = 0
    
    def __init__(self, **kwargs):
        HasProps.__init__(self, **kwargs)
        from zoof.ui.app import get_default_app
        self._app = get_default_app()
        Mirrored._counter += 1
        self._id = self.__class__.__name__ + str(Mirrored._counter)
        
        import json
        clsname = self.__class__.__name__
        props = {}
        for name in self.props():
            props[name] = getattr(self, name)
        cmd = 'zoof.widgets.%s = new zoof.%s(%s);' % (self.id, clsname, json.dumps(props))
        print(cmd)
        self._app._exec(cmd)
        
        # todo: get notified when a prop changes, pend a call via call_later
        # todo: collect more changed props if they come by
        # todo: in the callback send all prop updates to js
    
    def get_app(self):
        return self._app
    
    @property
    def id(self):
        return self._id
    
    def methoda(self):
        """ this is method a """
        pass
    
    @js
    def test_js_method(self):
        alert('Testing!')
    
    @classmethod
    def get_js(cls):
        cls_name = cls.__name__
        js = []
        # Main functions
        # todo: zoof.classes.xx
        js.append('zoof.%s = function (props) {' % cls_name)
        #js.append('    zoof.widgets[id] = this;')  # Just do zoof.widgets[id] = new XX
        js.append('    for (var name in props) {')
        js.append('        if (props.hasOwnProperty(name)) {')
        js.append('            this["_" + name] = props[name];')
        js.append('        }')
        js.append('    }')
        js.append('};')
        # Property setters and getters
        # todo: do we need *all* properties to be mirrored in JS?
        # todo: we could reduce JS code by doing inheritance in JS
        for name in cls.props():  # todo: only works if there was once an instance
            js.append('zoof.%s.prototype.set_%s = function (val) {' % (cls_name, name))
            js.append('    this._%s = val;' % name)
            js.append('};')
            js.append('zoof.%s.prototype.get_%s = function () {' % (cls_name, name))
            js.append('    return this._%s;' % name)
            js.append('};')
        # Methods
        for name in dir(cls):
            func = getattr(cls, name)
            if hasattr(func, 'js'):
                code = func.js.jscode.split(' ', 1)[1]  # todo: we now split on space in "var xxx = function ..."
                js.append('zoof.%s.prototype.%s' % (cls_name, code))
        return '\n'.join(js)


class Foo(HasProps):
    
    size = Int(help='the size of the foo')
    
    def __init__(self, x, **kwargs):
        HasProps.__init__(self, **kwargs)
        self._x = x
    
    def methodb(self):
        """ this is method b"""
        pass


class Widget(Mirrored):
    
    parent = Instance(Mirrored)  # todo: can we set ourselves?
    
    container_id = Str()  # used if parent is None
    
    def __init__(self, parent):
        # todo: -> parent is widget or ref to div element
        Mirrored.__init__(self)
        self._js_init()  # todo: allow a js __init__
    
    @js
    def _js_init(self):
        pass
    
    @js
    def set_cointainer_id(self, id):
        #if self._parent:
        #    return
        print('setting container id', id)
        el = document.getElementById(id)
        el.appendChild(this.node)
    
    def _repr_html_(self):
        container_id = self.id + '_container'
        # Set container id, this gets applied in the next event loop
        # iteration, so by the time it gets called in JS, the div that
        # we define below will have been created.
        from .app import call_later
        call_later(0, self.set_cointainer_id, container_id) # todo: always do calls in next iter
        return "<div id=%s />" % container_id


class Button(Widget):
    
    text = Str()
    
    def __init__(self):
        Mirrored.__init__(self)
        self._js_init()  # todo: allow a js __init__
    
    @js
    def _js_init(self):
        # todo: allow setting a placeholder DOM element, or any widget parent
        this.node = document.createElement('button')
        zoof.get('body').appendChild(this.node);
        this.node.innerHTML = 'Look, a button!'
    
    @js
    def set_text(self, txt):
        this.node.innerHTML = txt


class Bar(Foo):
    color = Color
    names = Tuple(str)
    

if __name__ == '__main__':
    a = Bar(1, size=4)
#Foo.size.__doc__ = 'asd'
    