"""
plotly
======

A module that contains the plotly class, a liaison between the user
and ploty's servers.

1. get DEFAULT_PLOT_OPTIONS for options

2. update plot_options with .plotly/ dir

3. update plot_options with _plot_options

4. update plot_options with kwargs!

"""
from __future__ import absolute_import

import base64
import copy
import json
import socket
import os
import threading
import time
import warnings
from collections import deque

import requests
import six
import six.moves
import trollius
from trollius import From, Return, TimeoutError

from plotly import exceptions, tools, utils, version
from plotly.plotly import chunked_requests
from plotly.session import (sign_in, update_session_plot_options,
                            get_session_plot_options, get_session_credentials,
                            get_session_config)
from plotly.utils import HttpResponseParser, DisconnectThread

__all__ = None

DEFAULT_PLOT_OPTIONS = {
    'filename': "plot from API",
    'fileopt': "new",
    'world_readable': True,
    'auto_open': True,
    'validate': True
}

# test file permissions and make sure nothing is corrupted
tools.ensure_local_plotly_files()


# don't break backwards compatibility
sign_in = sign_in
update_plot_options = update_session_plot_options


def get_credentials():
    """Returns the credentials that will be sent to plotly."""
    credentials = tools.get_credentials_file()
    session_credentials = get_session_credentials()
    for credentials_key in credentials:

        # checking for not false, but truthy value here is the desired behavior
        session_value = session_credentials.get(credentials_key)
        if session_value is False or session_value:
            credentials[credentials_key] = session_value
    return credentials


def get_config():
    """Returns either module config or file config."""
    config = tools.get_config_file()
    session_config = get_session_config()
    for config_key in config:

        # checking for not false, but truthy value here is the desired behavior
        session_value = session_config.get(config_key)
        if session_value is False or session_value:
            config[config_key] = session_value
    return config


def get_plot_options():
    """
    Merge default and user-defined plot options.

    """
    plot_options = copy.deepcopy(DEFAULT_PLOT_OPTIONS)
    session_plot_options = get_session_plot_options()
    for plot_option_key in plot_options:

        # checking for not false, but truthy value here is the desired behavior
        session_value = session_plot_options.get(plot_option_key)
        if session_value is False or session_value:
            plot_options[plot_option_key] = session_value
    return plot_options


def _plot_option_logic(plot_options):
    """
    Given some plot_options as part of a plot call, decide on final options

    """
    session_plot_options = get_session_plot_options()
    current_plot_options = get_plot_options()
    current_plot_options.update(plot_options)
    if (('filename' in plot_options or 'filename' in session_plot_options) and
            'fileopt' not in session_plot_options and
            'fileopt' not in plot_options):
        current_plot_options['fileopt'] = 'overwrite'
    return current_plot_options


def iplot(figure_or_data, **plot_options):
    """Create a unique url for this plot in Plotly and open in IPython.

    plot_options keyword agruments:
    filename (string) -- the name that will be associated with this figure
    fileopt ('new' | 'overwrite' | 'extend' | 'append')
        'new': create a new, unique url for this plot
        'overwrite': overwrite the file associated with `filename` with this
        'extend': add additional numbers (data) to existing traces
        'append': add additional traces to existing data lists
    world_readable (default=True) -- make this figure private/public

    """
    if 'auto_open' not in plot_options:
        plot_options['auto_open'] = False
    res = plot(figure_or_data, **plot_options)
    urlsplit = res.split('/')
    username, plot_id = urlsplit[-2][1:], urlsplit[-1]  # TODO: HACKY!

    embed_options = dict()
    if 'width' in plot_options:
        embed_options['width'] = plot_options['width']
    if 'height' in plot_options:
        embed_options['height'] = plot_options['height']

    return tools.embed(username, plot_id, **embed_options)


def plot(figure_or_data, validate=True, **plot_options):
    """Create a unique url for this plot in Plotly and optionally open url.

    plot_options keyword agruments:
    filename (string) -- the name that will be associated with this figure
    fileopt ('new' | 'overwrite' | 'extend' | 'append') -- 'new' creates a
        'new': create a new, unique url for this plot
        'overwrite': overwrite the file associated with `filename` with this
        'extend': add additional numbers (data) to existing traces
        'append': add additional traces to existing data lists
    world_readable (default=True) -- make this figure private/public
    auto_open (default=True) -- Toggle browser options
        True: open this plot in a new browser tab
        False: do not open plot in the browser, but do return the unique url

    """
    figure = tools.return_figure_from_figure_or_data(figure_or_data, validate)

    for entry in figure['data']:
        for key, val in list(entry.items()):
            try:
                if len(val) > 40000:
                    msg = ("Woah there! Look at all those points! Due to "
                           "browser limitations, Plotly has a hard time "
                           "graphing more than 500k data points for line "
                           "charts, or 40k points for other types of charts. "
                           "Here are some suggestions:\n"
                           "(1) Trying using the image API to return an image "
                           "instead of a graph URL\n"
                           "(2) Use matplotlib\n"
                           "(3) See if you can create your visualization with "
                           "fewer data points\n\n"
                           "If the visualization you're using aggregates "
                           "points (e.g., box plot, histogram, etc.) you can "
                           "disregard this warning.")
                    warnings.warn(msg)
            except TypeError:
                pass
    plot_options = _plot_option_logic(plot_options)
    res = _send_to_plotly(figure, **plot_options)
    if res['error'] == '':
        if plot_options['auto_open']:
            _open_url(res['url'])

        return res['url']
    else:
        raise exceptions.PlotlyAccountError(res['error'])


def iplot_mpl(fig, resize=True, strip_style=False, update=None, **plot_options):
    """Replot a matplotlib figure with plotly in IPython.

    This function:
    1. converts the mpl figure into JSON (run help(plolty.tools.mpl_to_plotly))
    2. makes a request to Plotly to save this figure in your account
    3. displays the image in your IPython output cell

    Positional agruments:
    fig -- a figure object from matplotlib

    Keyword arguments:
    resize (default=True) -- allow plotly to choose the figure size
    strip_style (default=False) -- allow plotly to choose style options
    update (default=None) -- update the resulting figure with an 'update'
        dictionary-like object resembling a plotly 'Figure' object

    Additional keyword arguments:
    plot_options -- run help(plotly.plotly.iplot)

    """
    fig = tools.mpl_to_plotly(fig, resize=resize, strip_style=strip_style)
    if update and isinstance(update, dict):
        try:
            fig.update(update)
            fig.validate()
        except exceptions.PlotlyGraphObjectError as err:
            err.add_note("Your updated figure could not be properly validated.")
            err.prepare()
            raise
    elif update is not None:
        raise exceptions.PlotlyGraphObjectError(
            "'update' must be dictionary-like and a valid plotly Figure "
            "object. Run 'help(plotly.graph_objs.Figure)' for more info."
        )
    return iplot(fig, **plot_options)


def plot_mpl(fig, resize=True, strip_style=False, update=None, **plot_options):
    """Replot a matplotlib figure with plotly.

    This function:
    1. converts the mpl figure into JSON (run help(plolty.tools.mpl_to_plotly))
    2. makes a request to Plotly to save this figure in your account
    3. opens your figure in a browser tab OR returns the unique figure url

    Positional agruments:
    fig -- a figure object from matplotlib

    Keyword arguments:
    resize (default=True) -- allow plotly to choose the figure size
    strip_style (default=False) -- allow plotly to choose style options
    update (default=None) -- update the resulting figure with an 'update'
        dictionary-like object resembling a plotly 'Figure' object

    Additional keyword arguments:
    plot_options -- run help(plotly.plotly.plot)

    """
    fig = tools.mpl_to_plotly(fig, resize=resize, strip_style=strip_style)
    if update and isinstance(update, dict):
        try:
            fig.update(update)
            fig.validate()
        except exceptions.PlotlyGraphObjectError as err:
            err.add_note("Your updated figure could not be properly validated.")
            err.prepare()
            raise
    elif update is not None:
        raise exceptions.PlotlyGraphObjectError(
            "'update' must be dictionary-like and a valid plotly Figure "
            "object. Run 'help(plotly.graph_objs.Figure)' for more info."
        )
    return plot(fig, **plot_options)


def get_figure(file_owner_or_url, file_id=None, raw=False):
    """Returns a JSON figure representation for the specified file

    Plotly uniquely identifies figures with a 'file_owner'/'file_id' pair.
    Since each file is given a corresponding unique url, you may also simply
    pass a valid plotly url as the first argument.

    Examples:
        fig = get_figure('https://plot.ly/~chris/1638')
        fig = get_figure('chris', 1638)

    Note, if you're using a file_owner string as the first argument, you MUST
    specify a `file_id` keyword argument. Else, if you're using a url string
    as the first argument, you MUST NOT specify a `file_id` keyword argument, or
    file_id must be set to Python's None value.

    Positional arguments:
    file_owner_or_url (string) -- a valid plotly username OR a valid plotly url

    Keyword arguments:
    file_id (default=None) -- an int or string that can be converted to int
                              if you're using a url, don't fill this in!
    raw (default=False) -- if true, return unicode JSON string verbatim**

    **by default, plotly will return a Figure object (run help(plotly
    .graph_objs.Figure)). This representation decodes the keys and values from
    unicode (if possible), removes information irrelevant to the figure
    representation, and converts the JSON dictionary objects to plotly
    `graph objects`.

    """
    plotly_rest_url = get_config()['plotly_domain']
    if file_id is None:  # assume we're using a url
        url = file_owner_or_url
        if url[:len(plotly_rest_url)] != plotly_rest_url:
            raise exceptions.PlotlyError(
                "Because you didn't supply a 'file_id' in the call, "
                "we're assuming you're trying to snag a figure from a url. "
                "You supplied the url, '{0}', we expected it to start with "
                "'{1}'."
                "\nRun help on this function for more information."
                "".format(url, plotly_rest_url))
        head = plotly_rest_url + "/~"
        file_owner = url.replace(head, "").split('/')[0]
        file_id = url.replace(head, "").split('/')[1]
    else:
        file_owner = file_owner_or_url
    resource = "/apigetfile/{username}/{file_id}".format(username=file_owner,
                                                         file_id=file_id)
    credentials = get_credentials()
    validate_credentials(credentials)
    username, api_key = credentials['username'], credentials['api_key']
    headers = {'plotly-username': username,
               'plotly-apikey': api_key,
               'plotly-version': version.__version__,
               'plotly-platform': 'python'}
    try:
        int(file_id)
    except ValueError:
        raise exceptions.PlotlyError(
            "The 'file_id' argument was not able to be converted into an "
            "integer number. Make sure that the positional 'file_id' argument "
            "is a number that can be converted into an integer or a string "
            "that can be converted into an integer."
        )
    if int(file_id) < 0:
        raise exceptions.PlotlyError(
            "The 'file_id' argument must be a non-negative number."
        )
    response = requests.get(plotly_rest_url + resource,
                            headers=headers,
                            verify=get_config()['plotly_ssl_verification'])
    if response.status_code == 200:
        if six.PY3:
            content = json.loads(response.content.decode('utf-8'))
        else:
            content = json.loads(response.content)
        response_payload = content['payload']
        figure = response_payload['figure']
        utils.decode_unicode(figure)
        if raw:
            return figure
        else:
            return tools.get_valid_graph_obj(figure, obj_type='Figure')
    else:
        try:
            content = json.loads(response.content)
            raise exceptions.PlotlyError(content)
        except:
            raise exceptions.PlotlyError(
                "There was an error retrieving this file")


class StreamWriter(object):
    """
    Class to help stream to Plotly's streaming server.

    """
    linear_back_off_rate = 0.250  # additional seconds per retry
    linear_wait_max = 16  # seconds

    exponential_back_off_base = 5  # base for exponentiation
    exponential_wait_max = 320  # seconds

    eol = '\r\n'  # end of line character for our http communication

    def __init__(self, token, ignore_status_codes=(None, 200),
                 ignore_errors=(), queue_length=500, initial_write_timeout=4):
        """
        Initialize a StreamWriter which can write to Plotly's streaming server.

        :param (str) token: The Plotly streaming token which links a trace to
            this data stream. *Not* your Plotly api_key.

        :param ignore_status_codes: Http status codes to ignore from server and
            reconnect on.

        :param (int) queue_length: The maximum number of messages to store if
            we're waiting to communicate with server.

        :param (int) initial_write_timeout: Seconds to wait for a response from
            the streaming server after the initial http request is sent.

        :param ignore_errors: Exception classes to ignore and reconnect on.
            Useful if you want quietly reconnect on network hiccups.

        """
        self.token = token
        self.ignore_status_codes = ignore_status_codes
        self.ignore_errors = ignore_errors
        self.queue_length = queue_length
        self.initial_write_timeout = initial_write_timeout

        self._queue = deque(maxlen=self.queue_length)  # prevent memory leaks
        self._last_beat = None

        self._connect_errors = 0
        self._last_connect_error = time.time()
        self._disconnections = 0
        self._last_disconnection = time.time()

        self._thread = None
        self._reader = None
        self._writer = None

        self._error = None
        self._response = None

        # hold reference to germain credentials/config at instantiation time.
        self.host = get_config()['plotly_streaming_domain']
        self.port = 80

        self._headers = {'Plotly-Streamtoken': self.token, 'Host': self.host,
                         'Transfer-Encoding': 'chunked',
                         'Content-Type': 'text/plain'}

    def _reset(self):
        """Prep some attributes to reconnect."""
        self._response = None
        self._error = None
        self._last_beat = None
        self._reader = None
        self._writer = None

    def _back_off_linearly(self):
        """Back off linearly if connect exceptions are thrown in the thread."""
        now = time.time()
        if now - self._last_connect_error > self.linear_wait_max * 2:
            self._connect_errors = 0
        self._last_connect_error = now

        self._connect_errors += 1
        wait_time = self._connect_errors * self.linear_back_off_rate
        if wait_time > self.linear_wait_max:
            raise exceptions.TooManyConnectFailures
        else:
            time.sleep(wait_time)

    def _back_off_exponentially(self):
        """Back off exponentially if peer keeps disconnecting."""
        now = time.time()
        if now - self._last_disconnection > self.exponential_wait_max * 2:
            self._disconnections = 0
        self._last_disconnection = now

        self._disconnections += 1
        wait_time = self.exponential_back_off_base ** self._disconnections
        if wait_time > self.exponential_wait_max:
            raise exceptions.TooManyConnectFailures
        else:
            time.sleep(wait_time)

    def _raise_for_response(self):
        """If we got a response, the server disconnected. Possibly raise."""
        if self._response is None:
            return

        try:
            response = self.response
            message = response.read()
            status = response.status
        except (AttributeError, six.moves.http_client.BadStatusLine):
            message = ''
            status = None

        if status not in self.ignore_status_codes:
            raise exceptions.ClosedConnection(message, status_code=status)

        # if we didn't raise here, we need to at least back off
        if status >= 500:
            self._back_off_linearly()  # it's the server's fault.
        else:
            self._back_off_exponentially()  # it's the client's fault.

    def _raise_for_error(self):
        """If there was an error during reading/writing, possibly raise."""
        if self._error is None:
            return

        if not isinstance(self._error, self.ignore_errors):
            raise self._error

        # if we didn't raise here, we need to at least back off
        if isinstance(self._error, socket.error):
            self._back_off_linearly()

        # TODO: do we need to dive into the socket error numbers here?

    def _check_pulse(self):
        """Streams get shut down after 60 seconds of inactivity."""
        self._last_beat = self._last_beat or time.time()
        now = time.time()
        if now - self._last_beat > 30:
            self._last_beat = now
            if not self._queue:
                self.write('')

    def _initial_write(self):
        """Write our request-line and readers with a blank body."""
        self._writer.write('POST / HTTP/1.1')
        self._writer.write(self.eol)
        for header, header_value in self._headers.items():
            self._writer.write('{}: {}'.format(header, header_value))
            self._writer.write(self.eol)
        self._writer.write(self.eol)

    def _write(self):
        """Check the queue, if it's not empty, write a chunk!"""
        if self._queue:
            self._chunk = self._queue.pop()
            hex_len = format(len(self._chunk), 'x')
            self._writer.write()
            message = '{}\r\n{}\r\n'.format(hex_len, self._chunk)
            self._writer.write(message.encode('utf-8'))

    @trollius.coroutine
    def _read(self, timeout=0.01):
        """
        Read the whole buffer or return None if nothing's there.

        :return: (str|None)

        """
        try:
            data = yield From(trollius.wait_for(self._reader.read(),
                                                timeout=timeout))
        except TimeoutError:
            data = None

        # This is how we return from coroutines with trollius
        raise Return(data)

    @trollius.coroutine
    def _read_write(self):
        """
        Entry point into coroutine functionality.

        Creates a reader and a writer by opening a connection to Plotly's
        streaming server. Then, it loops forever!

        """
        try:
            # open up a connection and get our StreamReader/StreamWriter
            current_thread = threading.current_thread()
            self._reader, self._writer = yield From(trollius.wait_for(
                trollius.open_connection(self.host, self.port), timeout=60
            ))

            # wait for an initial failure response, e.g., bad headers
            self._initial_write()
            self._response = yield From(self._read(self.initial_write_timeout))

            # read/write until server responds or thread is disconnected.
            while self._response is None and current_thread.connected:
                self._check_pulse()
                self._response = yield From(self._read())  # usually just None.
                self._write()

        except Exception as e:
            self._error = e
        finally:
            if self._writer:
                self._writer.close()

    def _thread_func(self, loop):
        """
        StreamWriters have threads that loop coroutines forever.

        :param loop: From `trollius.get_event_loop()`

        """
        trollius.set_event_loop(loop)
        loop.run_until_complete(self._read_write())

    @property
    def connected(self):
        """Returns state of the StreamWriter's thread."""
        return self._thread and self._thread.isAlive()

    @property
    def response(self):
        """Return a response object parsed from server string response."""
        if self._response is None:
            return None
        return HttpResponseParser(self._response).get_response()

    def open(self):
        """
        Standard `open` API. Strictly unnecessary as it opens on `write`.

        If not already opened, start a new daemon thread to consume the buffer.

        """
        if self.connected:
            return

        # if we've previously hit a failure, we should let the user know
        self._raise_for_response()
        self._raise_for_error()

        # reset our error, response, pulse information, etc.
        self._reset()

        # start up a new daemon thread
        loop = trollius.get_event_loop()
        self._thread = DisconnectThread(target=self._thread_func, args=(loop,))
        self._thread.setDaemon(True)
        self._thread.start()

    def write(self, chunk):
        """
        Standard `write` API.

        Reopens connection if closed and appends chunk to buffer.

        If chunk isn't a str|unicode, `json.dumps` is called with chunk.

        :param (str|unicode|dict) chunk: The data to be queued to send.

        """
        if not self.connected:
            self.open()

        if not isinstance(chunk, six.string_types):
            chunk = json.dumps(chunk, cls=utils.PlotlyJSONEncoder)

        self._queue.appendleft(chunk + '\n')  # needs at least 1 trailing '\n'

    def close(self):
        """
        Standard `close` API.

        Ensures thread is disconnected.

        """
        if not self.connected:
            return

        self._thread.disconnect()  # lets thread know to stop communicating
        self._thread = None


@utils.template_doc(**tools.get_config_file())
class Stream:
    """
    Interface to Plotly's real-time graphing API.

    Initialize a Stream object with a stream_id
    found in {plotly_domain}/settings.
    Real-time graphs are initialized with a call to `plot` that embeds
    your unique `stream_id`s in each of the graph's traces. The `Stream`
    interface plots data to these traces, as identified with the unique
    stream_id, in real-time.
    Every viewer of the graph sees the same data at the same time.

    View examples and tutorials here:
    https://plot.ly/python/streaming/

    Stream example:
    # Initialize a streaming graph
    # by embedding stream_id's in the graph's traces
    import plotly.plotly as py
    from plotly.graph_objs import Data, Scatter, Stream
    stream_id = "your_stream_id" # See {plotly_domain}/settings
    py.plot(Data([Scatter(x=[], y=[],
                          stream=Stream(token=stream_id, maxpoints=100))]))
    # Stream data to the import trace
    stream = Stream(stream_id) # Initialize a stream object
    stream.open() # Open the stream
    stream.write(dict(x=1, y=1)) # Plot (1, 1) in your graph

    """

    @utils.template_doc(**tools.get_config_file())
    def __init__(self, stream_id):
        """
        Initialize a Stream object with your unique stream_id.
        Find your stream_id at {plotly_domain}/settings.

        For more help, see: `help(plotly.plotly.Stream)`
        or see examples and tutorials here:
        https://plot.ly/python/streaming/

        """
        self.stream_id = stream_id
        self.connected = False
        self._stream = None

    def heartbeat(self, reconnect_on=(200, '', 408)):
        """
        Keep stream alive. Streams will close after ~1 min of inactivity.

        If the interval between stream writes is > 30 seconds, you should
        consider adding a heartbeat between your stream.write() calls like so:
        >>> stream.heartbeat()

        """
        try:
            self._stream.write('\n', reconnect_on=reconnect_on)
        except AttributeError:
            raise exceptions.PlotlyError(
                "Stream has not been opened yet, "
                "cannot write to a closed connection. "
                "Call `open()` on the stream to open the stream."
            )

    def open(self):
        """
        Open streaming connection to plotly.

        For more help, see: `help(plotly.plotly.Stream)`
        or see examples and tutorials here:
        https://plot.ly/python/streaming/

        """
        streaming_url = get_config()['plotly_streaming_domain']
        self._stream = chunked_requests.Stream(
            streaming_url, 80, {'Host': streaming_url,
                                'plotly-streamtoken': self.stream_id})

    def write(self, trace, layout=None, validate=True,
              reconnect_on=(200, '', 408)):
        """
        Write to an open stream.

        Once you've instantiated a 'Stream' object with a 'stream_id',
        you can 'write' to it in real time.

        positional arguments:
        trace - A valid plotly trace object (e.g., Scatter, Heatmap, etc.).
                Not all keys in these are `stremable` run help(Obj) on the type
                of trace your trying to stream, for each valid key, if the key
                is streamable, it will say 'streamable = True'. Trace objects
                must be dictionary-like.

        keyword arguments:
        layout (default=None) - A valid Layout object
                                Run help(plotly.graph_objs.Layout)
        validate (default = True) - Validate this stream before sending?
                                    This will catch local errors if set to True.

        Some valid keys for trace dictionaries:
            'x', 'y', 'text', 'z', 'marker', 'line'

        Examples:
        >>> write(dict(x=1, y=2))  # assumes 'scatter' type
        >>> write(Bar(x=[1, 2, 3], y=[10, 20, 30]))
        >>> write(Scatter(x=1, y=2, text='scatter text'))
        >>> write(Scatter(x=1, y=3, marker=Marker(color='blue')))
        >>> write(Heatmap(z=[[1, 2, 3], [4, 5, 6]]))

        The connection to plotly's servers is checked before writing
        and reconnected if disconnected and if the response status code
        is in `reconnect_on`.

        For more help, see: `help(plotly.plotly.Stream)`
        or see examples and tutorials here:
        http://nbviewer.ipython.org/github/plotly/python-user-guide/blob/master/s7_streaming/s7_streaming.ipynb

        """
        stream_object = dict()
        stream_object.update(trace)
        if 'type' not in stream_object:
            stream_object['type'] = 'scatter'
        if validate:
            try:
                tools.validate(stream_object, stream_object['type'])
            except exceptions.PlotlyError as err:
                raise exceptions.PlotlyError(
                    "Part of the data object with type, '{0}', is invalid. "
                    "This will default to 'scatter' if you do not supply a "
                    "'type'. If you do not want to validate your data objects "
                    "when streaming, you can set 'validate=False' in the call "
                    "to 'your_stream.write()'. Here's why the object is "
                    "invalid:\n\n{1}".format(stream_object['type'], err)
                )
            try:
                tools.validate_stream(stream_object, stream_object['type'])
            except exceptions.PlotlyError as err:
                raise exceptions.PlotlyError(
                    "Part of the data object with type, '{0}', cannot yet be "
                    "streamed into Plotly. If you do not want to validate "
                    "your data objects when streaming, you can set "
                    "'validate=False' in the call to 'your_stream.write()'. "
                    "Here's why the object cannot be streamed:\n\n{1}"
                    .format(stream_object['type'], err)
                )
            if layout is not None:
                try:
                    tools.validate(layout, 'Layout')
                except exceptions.PlotlyError as err:
                    raise exceptions.PlotlyError(
                        "Your layout kwarg was invalid. "
                        "Here's why:\n\n{0}".format(err)
                    )
        del stream_object['type']

        if layout is not None:
            stream_object.update(dict(layout=layout))

        # TODO: allow string version of this?
        jdata = json.dumps(stream_object, cls=utils.PlotlyJSONEncoder)
        jdata += "\n"

        try:
            self._stream.write(jdata, reconnect_on=reconnect_on)
        except AttributeError:
            raise exceptions.PlotlyError(
                "Stream has not been opened yet, "
                "cannot write to a closed connection. "
                "Call `open()` on the stream to open the stream.")

    def close(self):
        """
        Close the stream connection to plotly's streaming servers.

        For more help, see: `help(plotly.plotly.Stream)`
        or see examples and tutorials here:
        https://plot.ly/python/streaming/

        """
        try:
            self._stream.close()
        except AttributeError:
            raise exceptions.PlotlyError("Stream has not been opened yet.")


class image:
    """
    Helper functions wrapped around plotly's static image generation api.

    """
    @staticmethod
    def get(figure_or_data, format='png', width=None, height=None):
        """
        Return a static image of the plot described by `figure`.

        Valid formats: 'png', 'svg', 'jpeg', 'pdf'

        """
        # TODO: format is a built-in name... we shouldn't really use it
        if isinstance(figure_or_data, dict):
            figure = figure_or_data
        elif isinstance(figure_or_data, list):
            figure = {'data': figure_or_data}
        else:
            raise exceptions.PlotlyEmptyDataError(
                "`figure_or_data` must be a dict or a list."
            )

        if format not in ['png', 'svg', 'jpeg', 'pdf']:
            raise exceptions.PlotlyError(
                "Invalid format. This version of your Plotly-Python "
                "package currently only supports png, svg, jpeg, and pdf. "
                "Learn more about image exporting, and the currently "
                "supported file types here: "
                "https://plot.ly/python/static-image-export/"
            )

        headers = _api_v2.headers()
        headers['plotly_version'] = version.__version__
        headers['content-type'] = 'application/json'

        payload = {'figure': figure, 'format': format}
        if width is not None:
            payload['width'] = width
        if height is not None:
            payload['height'] = height

        url = _api_v2.api_url('images/')

        res = requests.post(
            url, data=json.dumps(payload, cls=utils.PlotlyJSONEncoder),
            headers=headers, verify=get_config()['plotly_ssl_verification'],
        )

        headers = res.headers

        if res.status_code == 200:
            if ('content-type' in headers and
                headers['content-type'] in ['image/png', 'image/jpeg',
                                            'application/pdf',
                                            'image/svg+xml']):
                return res.content

            elif ('content-type' in headers and
                  'json' in headers['content-type']):
                return_data = json.loads(res.content)
                return return_data['image']
        else:
            try:
                if ('content-type' in headers and
                        'json' in headers['content-type']):
                    return_data = json.loads(res.content)
                else:
                    return_data = {'error': res.content}
            except:
                raise exceptions.PlotlyError("The response "
                                             "from plotly could "
                                             "not be translated.")
            raise exceptions.PlotlyError(return_data['error'])


    @classmethod
    def ishow(cls, figure_or_data, format='png', width=None, height=None):
        """
        Display a static image of the plot described by `figure`
        in an IPython Notebook.

        """
        if format == 'pdf':
            raise exceptions.PlotlyError("Aw, snap! "
                "It's not currently possible to embed a pdf into "
                "an IPython notebook. You can save the pdf "
                "with the `image.save_as` or you can "
                "embed an png, jpeg, or svg.")
        img = cls.get(figure_or_data, format, width, height)
        from IPython.display import display, Image, SVG
        if format == 'svg':
            display(SVG(img))
        else:
            display(Image(img))

    @classmethod
    def save_as(cls, figure_or_data, filename, format=None, width=None,
                height=None):
        """
        Save a image of the plot described by `figure` locally as `filename`.

        Valid image formats are 'png', 'svg', 'jpeg', and 'pdf'.
        The format is taken as the extension of the filename or as the
        supplied format.

        """
        # todo: format shadows built-in name
        (base, ext) = os.path.splitext(filename)
        if not ext and not format:
            filename += '.png'
        elif ext and not format:
            format = ext[1:]
        elif not ext and format:
            filename += '.'+format
        else:
            filename += '.'+format

        img = cls.get(figure_or_data, format, width, height)

        f = open(filename, 'wb')
        f.write(img)
        f.close()


class file_ops:
    """
    Interface to Plotly's File System API

    """

    @classmethod
    def mkdirs(cls, folder_path):
        """
        Create folder(s) specified by folder_path in your Plotly account.

        If the intermediate directories do not exist,
        they will be created. If they already exist,
        no error will be thrown.

        Mimics the shell's mkdir -p.

        Returns:
        - 200 if folders already existed, nothing was created
        - 201 if path was created
        Raises:
        -  exceptions.PlotlyRequestError with status code
           400 if the path already exists.

        Usage:
        >> mkdirs('new folder')
        >> mkdirs('existing folder/new folder')
        >> mkdirs('new/folder/path')

        """
        # trim trailing slash TODO: necessesary?
        if folder_path[-1] == '/':
            folder_path = folder_path[0:-1]

        payload = {
            'path': folder_path
        }

        url = _api_v2.api_url('folders')

        res = requests.post(url, data=payload, headers=_api_v2.headers(),
                            verify=get_config()['plotly_ssl_verification'])

        _api_v2.response_handler(res)

        return res.status_code


class grid_ops:
    """
    Interface to Plotly's Grid API.
    Plotly Grids are Plotly's tabular data object, rendered
    in an online spreadsheet. Plotly graphs can be made from
    references of columns of Plotly grid objects. Free-form
    JSON Metadata can be saved with Plotly grids.

    To create a Plotly grid in your Plotly account from Python,
    see `grid_ops.upload`.

    To add rows or columns to an existing Plotly grid, see
    `grid_ops.append_rows` and `grid_ops.append_columns`
    respectively.

    To delete one of your grid objects, see `grid_ops.delete`.

    """
    @classmethod
    def _fill_in_response_column_ids(cls, request_columns,
                                     response_columns, grid_id):
        for req_col in request_columns:
            for resp_col in response_columns:
                if resp_col['name'] == req_col.name:
                    req_col.id = '{0}/{1}'.format(grid_id, resp_col['uid'])
                    response_columns.remove(resp_col)

    @classmethod
    def upload(cls, grid, filename,
               world_readable=True, auto_open=True, meta=None):
        """
        Upload a grid to your Plotly account with the specified filename.

        Positional arguments:
            - grid: A plotly.grid_objs.Grid object,
                    call `help(plotly.grid_ops.Grid)` for more info.
            - filename: Name of the grid to be saved in your Plotly account.
                        To save a grid in a folder in your Plotly account,
                        separate specify a filename with folders and filename
                        separated by backslashes (`/`).
                        If a grid, plot, or folder already exists with the same
                        filename, a `plotly.exceptions.RequestError` will be
                        thrown with status_code 409

        Optional keyword arguments:
            - world_readable (default=True): make this grid publically (True)
                                             or privately (False) viewable.
            - auto_open (default=True): Automatically open this grid in
                                        the browser (True)
            - meta (default=None): Optional Metadata to associate with
                                   this grid.
                                   Metadata is any arbitrary
                                   JSON-encodable object, for example:
                                   `{"experiment name": "GaAs"}`

        Filenames must be unique. To overwrite a grid with the same filename,
        you'll first have to delete the grid with the blocking name. See
        `plotly.plotly.grid_ops.delete`.

        Usage example 1: Upload a plotly grid
        ```
        from plotly.grid_objs import Grid, Column
        import plotly.plotly as py
        column_1 = Column([1, 2, 3], 'time')
        column_2 = Column([4, 2, 5], 'voltage')
        grid = Grid([column_1, column_2])
        py.grid_ops.upload(grid, 'time vs voltage')
        ```

        Usage example 2: Make a graph based with data that is sourced
                         from a newly uploaded Plotly grid
        ```
        import plotly.plotly as py
        from plotly.grid_objs import Grid, Column
        from plotly.graph_objs import Scatter
        # Upload a grid
        column_1 = Column([1, 2, 3], 'time')
        column_2 = Column([4, 2, 5], 'voltage')
        grid = Grid([column_1, column_2])
        py.grid_ops.upload(grid, 'time vs voltage')

        # Build a Plotly graph object sourced from the
        # grid's columns
        trace = Scatter(xsrc=grid[0], ysrc=grid[1])
        py.plot([trace], filename='graph from grid')
        ```

        """
        # Make a folder path
        if filename[-1] == '/':
            filename = filename[0:-1]

        paths = filename.split('/')
        parent_path = '/'.join(paths[0:-1])
        filename = paths[-1]

        if parent_path != '':
            file_ops.mkdirs(parent_path)

        # transmorgify grid object into plotly's format
        grid_json = grid._to_plotly_grid_json()
        if meta is not None:
            grid_json['metadata'] = meta

        payload = {
            'filename': filename,
            'data': json.dumps(grid_json, cls=utils.PlotlyJSONEncoder),
            'world_readable': world_readable
        }

        if parent_path != '':
            payload['parent_path'] = parent_path

        upload_url = _api_v2.api_url('grids')
        req = requests.post(upload_url, data=payload,
                            headers=_api_v2.headers(),
                            verify=get_config()['plotly_ssl_verification'])

        res = _api_v2.response_handler(req)

        response_columns = res['file']['cols']
        grid_id = res['file']['fid']
        grid_url = res['file']['web_url']

        # mutate the grid columns with the id's returned from the server
        cls._fill_in_response_column_ids(grid, response_columns, grid_id)

        grid.id = grid_id

        if meta is not None:
            meta_ops.upload(meta, grid=grid)

        if auto_open:
            _open_url(grid_url)

        return grid_url

    @classmethod
    def append_columns(cls, columns, grid=None, grid_url=None):
        """
        Append columns to a Plotly grid.

        `columns` is an iterable of plotly.grid_objs.Column objects
        and only one of `grid` and `grid_url` needs to specified.

        `grid` is a ploty.grid_objs.Grid object that has already been
        uploaded to plotly with the grid_ops.upload method.

        `grid_url` is a unique URL of a `grid` in your plotly account.

        Usage example 1: Upload a grid to Plotly, and then append a column
        ```
        from plotly.grid_objs import Grid, Column
        import plotly.plotly as py
        column_1 = Column([1, 2, 3], 'time')
        grid = Grid([column_1])
        py.grid_ops.upload(grid, 'time vs voltage')

        # append a column to the grid
        column_2 = Column([4, 2, 5], 'voltage')
        py.grid_ops.append_columns([column_2], grid=grid)
        ```

        Usage example 2: Append a column to a grid that already exists on Plotly
        ```
        from plotly.grid_objs import Grid, Column
        import plotly.plotly as py

        grid_url = 'https://plot.ly/~chris/3143'
        column_1 = Column([1, 2, 3], 'time')
        py.grid_ops.append_columns([column_1], grid_url=grid_url)
        ```

        """
        grid_id = _api_v2.parse_grid_id_args(grid, grid_url)

        # Verify unique column names
        column_names = [c.name for c in columns]
        if grid:
            existing_column_names = [c.name for c in grid]
            column_names.extend(existing_column_names)
        duplicate_name = utils.get_first_duplicate(column_names)
        if duplicate_name:
            err = exceptions.NON_UNIQUE_COLUMN_MESSAGE.format(duplicate_name)
            raise exceptions.InputError(err)

        payload = {
            'cols': json.dumps(columns, cls=utils.PlotlyJSONEncoder)
        }

        api_url = (_api_v2.api_url('grids') +
                   '/{grid_id}/col'.format(grid_id=grid_id))
        res = requests.post(api_url, data=payload, headers=_api_v2.headers(),
                            verify=get_config()['plotly_ssl_verification'])
        res = _api_v2.response_handler(res)

        cls._fill_in_response_column_ids(columns, res['cols'], grid_id)

        if grid:
            grid.extend(columns)

    @classmethod
    def append_rows(cls, rows, grid=None, grid_url=None):
        """
        Append rows to a Plotly grid.

        `rows` is an iterable of rows, where each row is a
        list of numbers, strings, or dates. The number of items
        in each row must be equal to the number of columns
        in the grid. If appending rows to a grid with columns of
        unequal length, Plotly will fill the columns with shorter
        length with empty strings.

        Only one of `grid` and `grid_url` needs to specified.

        `grid` is a ploty.grid_objs.Grid object that has already been
        uploaded to plotly with the grid_ops.upload method.

        `grid_url` is a unique URL of a `grid` in your plotly account.

        Usage example 1: Upload a grid to Plotly, and then append rows
        ```
        from plotly.grid_objs import Grid, Column
        import plotly.plotly as py
        column_1 = Column([1, 2, 3], 'time')
        column_2 = Column([5, 2, 7], 'voltage')
        grid = Grid([column_1, column_2])
        py.grid_ops.upload(grid, 'time vs voltage')

        # append a row to the grid
        row = [1, 5]
        py.grid_ops.append_rows([row], grid=grid)
        ```

        Usage example 2: Append a row to a grid that already exists on Plotly
        ```
        from plotly.grid_objs import Grid
        import plotly.plotly as py

        grid_url = 'https://plot.ly/~chris/3143'

        row = [1, 5]
        py.grid_ops.append_rows([row], grid=grid_url)
        ```

        """
        grid_id = _api_v2.parse_grid_id_args(grid, grid_url)

        if grid:
            n_columns = len([column for column in grid])
            for row_i, row in enumerate(rows):
                if len(row) != n_columns:
                    raise exceptions.InputError(
                        "The number of entries in "
                        "each row needs to equal the number of columns in "
                        "the grid. Row {0} has {1} {2} but your "
                        "grid has {3} {4}. "
                        .format(row_i, len(row),
                                'entry' if len(row) == 1 else 'entries',
                                n_columns,
                                'column' if n_columns == 1 else 'columns'))

        payload = {
            'rows': json.dumps(rows, cls=utils.PlotlyJSONEncoder)
        }

        api_url = (_api_v2.api_url('grids')+
                   '/{grid_id}/row'.format(grid_id=grid_id))
        res = requests.post(api_url, data=payload, headers=_api_v2.headers(),
                            verify=get_config()['plotly_ssl_verification'])
        _api_v2.response_handler(res)

        if grid:
            longest_column_length = max([len(col.data) for col in grid])

            for column in grid:
                n_empty_rows = longest_column_length - len(column.data)
                empty_string_rows = ['' for _ in range(n_empty_rows)]
                column.data.extend(empty_string_rows)

            column_extensions = zip(*rows)
            for local_column, column_extension in zip(grid, column_extensions):
                local_column.data.extend(column_extension)

    @classmethod
    def delete(cls, grid=None, grid_url=None):
        """
        Delete a grid from your Plotly account.

        Only one of `grid` or `grid_url` needs to be specified.

        `grid` is a plotly.grid_objs.Grid object that has already
               been uploaded to Plotly.

        `grid_url` is the URL of the Plotly grid to delete

        Usage example 1: Upload a grid to plotly, then delete it
        ```
        from plotly.grid_objs import Grid, Column
        import plotly.plotly as py
        column_1 = Column([1, 2, 3], 'time')
        column_2 = Column([4, 2, 5], 'voltage')
        grid = Grid([column_1, column_2])
        py.grid_ops.upload(grid, 'time vs voltage')

        # now delete it, and free up that filename
        py.grid_ops.delete(grid)
        ```

        Usage example 2: Delete a plotly grid by url
        ```
        import plotly.plotly as py

        grid_url = 'https://plot.ly/~chris/3'
        py.grid_ops.delete(grid_url=grid_url)
        ```

        """
        grid_id = _api_v2.parse_grid_id_args(grid, grid_url)
        api_url = _api_v2.api_url('grids')+'/'+grid_id
        res = requests.delete(api_url, headers=_api_v2.headers(),
                              verify=get_config()['plotly_ssl_verification'])
        _api_v2.response_handler(res)


class meta_ops:
    """
    Interface to Plotly's Metadata API.

    In Plotly, Metadata is arbitrary, free-form JSON data that is
    associated with Plotly grids. Metadata is viewable with any grid
    that is shared and grids are searchable by key value pairs in
    the Metadata. Metadata is any JSON-encodable object.

    To upload Metadata, either use the optional keyword argument `meta`
    in the `py.grid_ops.upload` method, or use `py.meta_ops.upload`.

    """

    @classmethod
    def upload(cls, meta, grid=None, grid_url=None):
        """
        Upload Metadata to a Plotly grid.

        Metadata is any JSON-encodable object. For example,
        a dictionary, string, or list.

        Only one of `grid` or `grid_url` needs to be specified.

        `grid` is a plotly.grid_objs.Grid object that has already
               been uploaded to Plotly.

        `grid_url` is the URL of the Plotly grid to attach Metadata to.

        Usage example 1: Upload a grid to Plotly, then attach Metadata to it
        ```
        from plotly.grid_objs import Grid, Column
        import plotly.plotly as py
        column_1 = Column([1, 2, 3], 'time')
        column_2 = Column([4, 2, 5], 'voltage')
        grid = Grid([column_1, column_2])
        py.grid_ops.upload(grid, 'time vs voltage')

        # now attach Metadata to the grid
        meta = {'experment': 'GaAs'}
        py.meta_ops.upload(meta, grid=grid)
        ```

        Usage example 2: Upload Metadata to an existing Plotly grid
        ```
        import plotly.plotly as py

        grid_url = 'https://plot.ly/~chris/3143'

        meta = {'experment': 'GaAs'}

        py.meta_ops.upload(meta, grid_url=grid_Url)
        ```

        """
        grid_id = _api_v2.parse_grid_id_args(grid, grid_url)

        payload = {
            'metadata': json.dumps(meta, cls=utils.PlotlyJSONEncoder)
        }

        api_url = _api_v2.api_url('grids')+'/{grid_id}'.format(grid_id=grid_id)

        res = requests.patch(api_url, data=payload, headers=_api_v2.headers(),
                             verify=get_config()['plotly_ssl_verification'])

        return _api_v2.response_handler(res)


class _api_v2:
    """
    Request and response helper class for communicating with Plotly's v2 API

    """
    @classmethod
    def parse_grid_id_args(cls, grid, grid_url):
        """
        Return the grid_id from the non-None input argument.

        Raise an error if more than one argument was supplied.

        """
        if grid is not None:
            id_from_grid = grid.id
        else:
            id_from_grid = None
        args = [id_from_grid, grid_url]
        arg_names = ('grid', 'grid_url')

        supplied_arg_names = [arg_name for arg_name, arg
                              in zip(arg_names, args) if arg is not None]

        if not supplied_arg_names:
            raise exceptions.InputError(
                "One of the two keyword arguments is required:\n"
                "    `grid` or `grid_url`\n\n"
                "grid: a plotly.graph_objs.Grid object that has already\n"
                "    been uploaded to Plotly.\n\n"
                "grid_url: the url where the grid can be accessed on\n"
                "    Plotly, e.g. 'https://plot.ly/~chris/3043'\n\n"
            )
        elif len(supplied_arg_names) > 1:
            raise exceptions.InputError(
                "Only one of `grid` or `grid_url` is required. \n"
                "You supplied both. \n"
            )
        else:
            supplied_arg_name = supplied_arg_names.pop()
            if supplied_arg_name == 'grid_url':
                path = six.moves.urllib.parse.urlparse(grid_url).path
                file_owner, file_id = path.replace("/~", "").split('/')[0:2]
                return '{0}:{1}'.format(file_owner, file_id)
            else:
                return grid.id

    @classmethod
    def response_handler(cls, response):

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as requests_exception:
            plotly_exception = exceptions.PlotlyRequestError(
                requests_exception
            )
            raise(plotly_exception)

        if ('content-type' in response.headers and
                'json' in response.headers['content-type'] and
                len(response.content) > 0):

            response_dict = json.loads(response.content.decode('utf8'))

            if 'warnings' in response_dict and len(response_dict['warnings']):
                warnings.warn('\n'.join(response_dict['warnings']))

            return response_dict

    @classmethod
    def api_url(cls, resource):
        return ('{0}/v2/{1}'.format(get_config()['plotly_api_domain'],
                resource))

    @classmethod
    def headers(cls):
        credentials = get_credentials()

        # todo, validate here?
        username, api_key = credentials['username'], credentials['api_key']
        encoded_api_auth = base64.b64encode(six.b('{0}:{1}'.format(
            username, api_key))).decode('utf8')

        headers = {
            'plotly-client-platform': 'python {0}'.format(version.__version__)
        }

        if get_config()['plotly_proxy_authorization']:
            proxy_username = credentials['proxy_username']
            proxy_password = credentials['proxy_password']
            encoded_proxy_auth = base64.b64encode(six.b('{0}:{1}'.format(
                proxy_username, proxy_password))).decode('utf8')
            headers['authorization'] = 'Basic ' + encoded_proxy_auth
            headers['plotly-authorization'] = 'Basic ' + encoded_api_auth
        else:
            headers['authorization'] = 'Basic ' + encoded_api_auth

        return headers


def validate_credentials(credentials):
    """
    Currently only checks for truthy username and api_key

    """
    username = credentials.get('username')
    api_key = credentials.get('api_key')
    if not username or not api_key:
        raise exceptions.PlotlyLocalCredentialsError()


def _send_to_plotly(figure, **plot_options):
    """

    """
    fig = tools._replace_newline(figure)  # does not mutate figure
    data = json.dumps(fig['data'] if 'data' in fig else [],
                      cls=utils.PlotlyJSONEncoder)
    credentials = get_credentials()
    validate_credentials(credentials)
    username = credentials['username']
    api_key = credentials['api_key']
    kwargs = json.dumps(dict(filename=plot_options['filename'],
                             fileopt=plot_options['fileopt'],
                             world_readable=plot_options['world_readable'],
                             layout=fig['layout'] if 'layout' in fig
                             else {}),
                        cls=utils.PlotlyJSONEncoder)

    # TODO: It'd be cool to expose the platform for RaspPi and others
    payload = dict(platform='python',
                   version=version.__version__,
                   args=data,
                   un=username,
                   key=api_key,
                   origin='plot',
                   kwargs=kwargs)

    url = get_config()['plotly_domain'] + "/clientresp"

    r = requests.post(url, data=payload,
                      verify=get_config()['plotly_ssl_verification'])
    r.raise_for_status()
    r = json.loads(r.text)
    if 'error' in r and r['error'] != '':
        print(r['error'])
    if 'warning' in r and r['warning'] != '':
        warnings.warn(r['warning'])
    if 'message' in r and r['message'] != '':
        print(r['message'])

    return r


def _open_url(url):
    try:
        from webbrowser import open as wbopen
        wbopen(url)
    except:  # TODO: what should we except here? this is dangerous
        pass
