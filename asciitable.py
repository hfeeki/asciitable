""" An extensible ASCII table reader.

:Copyright: Smithsonian Astrophysical Observatory (2009)
:Author: Tom Aldcroft (aldcroft@head.cfa.harvard.edu)
"""
import os
import sys
import re
import csv
import itertools

try:
    import numpy
    has_numpy = True
except ImportError:
    has_numpy = False

class InconsistentTableError(Exception):
    pass

class Column(object):
    def __init__(self, name, index):
        self.name = name
        self.index = index
        self.str_vals = []

class BaseInputter(object):
    def get_lines(self, table):
        """Get the lines from the ``table`` input which can be one of:

        * File name
        * String (newline separated) with all header and data lines (must have at least 2 lines)
        * List of strings

        :param table: table input
        :returns: list of lines
        """
        try:
            table + ''
            if os.linesep not in table:
                table = open(table, 'r').read()
            lines = table.splitlines()
        except TypeError:
            try:
                # See if table supports indexing, slicing, and iteration
                table[0]
                table[0:1]
                iter(table)
                lines = table
            except TypeError:
                raise TypeError('Input "table" must be a string (filename or data) or an iterable')

        return self.process_lines(lines)

    def process_lines(self, lines):
        """Process lines for subsequent use.  In the default case do nothing.

        Override this method if something more has to be done to convert
        raw input lines to the table rows.  For example one could account for
        continuation characters if a row is split into lines."""
        return lines
        # [x for x in lines if len(x.strip()) > 0]

class BaseSplitter(object):
    """Minimal splitter that just uses python's split method to do the work.

    This does not handle quoted values.  Key features are the call to
    self.preprocess if needed and the formulation of __call__ as a generator
    that returns a list of the split line values at each iteration.

    There are two methods that are intended to be overwritten, first
    ``process_line()`` to do pre-processing on each input line before splitting
    and ``process_val()`` to do post-processing on each split string value.  By
    default these apply the string ``strip()`` function.  These can be set to a
    number function via the instance attribute or be disabled entirely, e.g.::

      reader.header.splitter.process_val = lambda x: x.lstrip()
      reader.data.splitter.process_val = None
      
    """
    def process_line(self, x):
        """Remove whitespace at the beginning or end of line.  This is especially useful for
        whitespace-delimited files to prevent spurious columns at the beginning or end."""
        return x.strip()

    def process_val(self, x):
        """Remove whitespace at the beginning or end of value."""
        return x.strip()

    delimiter = None

    def __call__(self, lines):
        if self.process_line:
            lines = (self.process_line(x) for x in lines)
        for line in lines:
            vals = line.split(self.delimiter)
            if self.process_val:
                yield [self.process_val(x) for x in vals]
            else:
                yield vals

class DefaultSplitter(BaseSplitter):
    """Default class to split strings into columns using python csv.  The class
    attributes are taken from the csv Dialect class.

    Typical usage::

      # lines = ..
      splitter = asciitable.DefaultSplitter()
      for col_vals in splitter(lines):
          for col_val in col_vals:
               ...

    :param delimiter: one-character string used to separate fields.
    :param doublequote:  control how instances of *quotechar* in a field are quoted
    :param escapechar: character to remove special meaning from following character
    :param quotechar: one-character stringto quote fields containing special characters
    :param quoting: control when quotes are recognised by the reader
    :param skipinitialspace: ignore whitespace immediately following the delimiter
    """
    delimiter = ' '
    quotechar = '"'
    doublequote = True
    escapechar = None
    quoting = csv.QUOTE_MINIMAL
    skipinitialspace = True
    
    def __call__(self, lines):
        """Return an iterator over the table ``lines``, where each iterator output
        is a list of the split line values.

        :param lines: list of table lines
        :returns: iterator
        """
        if self.process_line:
            lines = [self.process_line(x) for x in lines]

        csv_reader = csv.reader(lines,
                                delimiter =self.delimiter,
                                doublequote = self.doublequote,
                                escapechar =self.escapechar,
                                quotechar = self.quotechar,
                                quoting = self.quoting,
                                skipinitialspace = self.skipinitialspace
                                )
        for vals in csv_reader:
            if self.process_val:
                yield [self.process_val(x) for x in vals]
            else:
                yield vals
            
        
def _get_line_index(line_or_func, lines):
    if hasattr(line_or_func, '__call__'):
        return line_or_func(lines)
    else:
        return line_or_func

class BaseHeader(object):
    """Base table header reader

    :param auto_format: format string for auto-generating column names
    :param start_line: None, int, or a function of ``lines`` that returns None or int
    :param comment: regular expression for comment lines
    :param splitter: Splitter object for splitting data lines into columns
    :param names: list of names corresponding to each data column
    :param include_names: list of names to include in output (default=None selects all names)
    :param exclude_names: list of names to exlude from output (applied after ``include_names``)
    """
    auto_format = 'col%d'
    start_line = None
    comment = None
    splitter_class = DefaultSplitter
    names = None
    include_names = None
    exclude_names = None

    def __init__(self):
        self.splitter = self.__class__.splitter_class()

    def get_cols(self, lines):
        """Initialize the header Column objects from the table ``lines``.

        Based on the previously set Header attributes find or create the column names.
        Sets ``self.col``s with the list of Columns.  This list only includes the actual
        requested columns after filtering by the include_names and exclude_names
        attributes.  See ``self.names`` for the full list.

        :param lines: list of table lines
        :returns: list of table Columns
        """
        # Get the data values from the first line of table data to determine n_data_cols
        first_data_vals = self.data.get_str_vals().next()
        n_data_cols = len(first_data_vals)

        start_line = _get_line_index(self.start_line, lines)
        if start_line is None:
            # No header line so auto-generate names from n_data_cols
            if self.names is None:
                self.names = [self.auto_format % i for i in range(1, n_data_cols+1)]

        elif self.names is None:
            # No column names supplied so read them from header line in table.
            for i, line in enumerate(self.process_lines(lines)):
                if i == start_line:
                    break
            else: # No header line matching
                raise ValueError('No header line found in table')

            self.names = self.splitter([line]).next()
        
        if len(self.names) != n_data_cols:
            errmsg = ('Number of header columns (%d) inconsistent with '
                      'data columns (%d) in first line\n'
                      'Header values: %s\n'
                      'Data values: %s' % (len(self.names), len(first_data_vals),
                                           self.names, first_data_vals))
            raise InconsistentTableError(errmsg)

        names = set(self.names)
        if self.include_names is not None:
            names.intersection_update(self.include_names)
        if self.exclude_names is not None:
            names.difference_update(self.exclude_names)
            
        self.cols = [Column(name=x, index=i) for i, x in enumerate(self.names) if x in names]

    def process_lines(self, lines):
        """Generator to yield non-comment lines"""
        if self.comment:
            re_comment = re.compile(self.comment)
        # Yield non-comment lines
        for line in lines:
            if line and not self.comment or not re_comment.match(line):
                yield line
                

class BaseData(object):
    """Table data reader

    :param start_line: None, int, or a function of ``lines`` that returns None or int
    :param end_line: None, int, or a function of ``lines`` that returns None or int
    :param comment: Regular expression for comment lines
    :param splitter: Splitter object for splitting data lines into columns
    :param fill_values: Dict of (bad_value: fill_value) pairs
    """
    start_line = None
    end_line = None
    comment = None
    splitter_class = DefaultSplitter
    fill_values = None
    
    def __init__(self):
        self.splitter = self.__class__.splitter_class()

    def process_lines(self, lines):
        """Strip out comment lines from list of ``lines``"""
        nonblank_lines = (x for x in lines if x.strip())
        if self.comment:
            re_comment = re.compile(self.comment)
            return [x for x in nonblank_lines if not re_comment.match(x)]
        else:
            return [x for x in nonblank_lines]

    def get_data_lines(self, lines):
        data_lines = self.process_lines(lines)
        start_line = _get_line_index(self.start_line, data_lines)
        end_line = _get_line_index(self.end_line, data_lines)

        if start_line is not None or end_line is not None:
            self.data_lines = data_lines[slice(start_line, self.end_line)]
        else:  # Don't copy entire data lines unless necessary
            self.data_lines = data_lines

    def get_str_vals(self):
        return self.splitter(self.data_lines)

    def set_masks(self, cols):
        if self.fill_values is not None:
            for col in cols:
                col.mask = [False] * len(col.str_vals)
                for i, str_val in ((i, x) for i, x in enumerate(col.str_vals) if x in fill_values):
                    col.str_vals[i] = fill_values[str_val]
                    col.mask[i] = True


class _DictLikeNumpy(dict):
    """Provide minimal compatibility with numpy rec array API for BaseOutputter
    object::
      
      table = asciitable.read('mytable.dat', numpy=False)
      table.field('x')    # access column 'x'
      table.dtype.names   # get column names in order
    """
    class Dtype(object):
        pass
    dtype = Dtype()
    def field(self, colname):
        return self[colname].data

    def __len__(self):
        return len(self.values()[0].data)

        
class BaseOutputter(object):
    """Output table as a dict of column objects keyed on column name.  The
    table data are stored as plain python lists within the column objects.

    Example::
    
      reader = asciitable.Table()
      reader.data.fill_values = {'': '-999'} # replace empty fields with -999
      table = reader.read('mytable.dat')
      table['x'].data # data for column 'x'
      table['y'].mask # mask list for column 'y'
      table.field('x')    # access column 'x'
      table.dtype.names   # get column names in order
    """
    converters = {}
    default_converter = [lambda vals: [int(x) for x in vals],
                         lambda vals: [float(x) for x in vals],
                         lambda vals: vals]

    def __call__(self, cols):
        self._convert_vals(cols)
        table = _DictLikeNumpy((x.name, x) for x in cols)
        table.dtype.names = tuple(x.name for x in cols)
        return table

    def _convert_vals(self, cols):
        for col in cols:
            converters = self.converters.get(col.name, self.default_converter)
            # Make a copy of converters and make sure converters it is a list
            try:
                col.converters = converters[:]
            except TypeError:
                col.converters = [converters]

            while not hasattr(col, 'data'):
                try:
                    col.data = col.converters[0](col.str_vals)
                except (TypeError, ValueError):
                    col.converters.pop(0)
                except IndexError:
                    raise ValueError('Column {0} failed to convert'.format(col.name))

def convert_numpy(numpy_type):
    """Return a function that converts a list into a numpy array of the given
    ``numpy_type``.  This type must be a string corresponding to a numpy type,
    e.g. 'int64' or 'str' or 'float32'.
    """
    def converter(vals):
        return numpy.array(vals, getattr(numpy, numpy_type))
    return converter

class NumpyOutputter(BaseOutputter):
    """Output the table as a numpy.rec.recarray

    :param default_converter: default list of functions tried (in order) to convert a column
    """

    coming_soon = """Missing or bad data values are handled at two levels.  The first is in
    the data reading step where if ``data.fill_values`` is set then any
    occurences of a bad value are replaced by the correspond fill value.
    At the same time a boolean list ``mask`` is created in the column object.

    The second stage is when converting to numpy arrays which can be either
    plain arrays or masked arrays.  Use the ``auto_masked`` and ``default_masked``
    to control this behavior as follows:

    ===========  ==============  ===========  ============
    auto_masked  default_masked  fill_values  output
    ===========  ==============  ===========  ============
    --            True           --           masked_array
    --            False          None         array
    True          --             dict(..)     masked_array
    False         --             dict(..)     array
    ===========  ==============  ===========  ============

    :param auto_masked: use masked arrays if data reader has ``fill_values`` set (default=True)
    :param default_masked: always use masked arrays (default=False)
    """

    auto_masked_array = True
    default_masked_array = False
    default_converter = [convert_numpy('int'),
                         convert_numpy('float'),
                         convert_numpy('str')]

    def __call__(self, cols):
        self._convert_vals(cols)
        return numpy.rec.fromarrays([x.data for x in cols], names=[x.name for x in cols])

class BaseReader(object):
    """Class providing methods to read an ASCII table using the specified
    header, data, inputter, and outputter instances.

    Typical usage is to instantiate a Reader() object and customize the
    ``header``, ``data``, ``inputter``, and ``outputter`` attributes.  Each
    of these is an object of the corresponding class.

    """
    def __init__(self):
        self.header = BaseHeader()
        self.data = BaseData()
        self.inputter = BaseInputter()
        self.outputter = BaseOutputter()

    def read(self, table):
        """Read the ``table`` and return the results in a format determined by
        the ``outputter`` attribute.

        The ``table`` parameter is any string or object that can be processed
        by the instance ``inputter``.  For the base Inputter class ``table`` can be
        one of:

        * File name
        * String (newline separated) with all header and data lines (must have at least 2 lines)
        * List of strings

        :param table: table input
        :returns: output table
        """
        # Data and Header instances benefit from a little cross-coupling.  Header may need to
        # know about number of data columns for auto-column name generation and Data may
        # need to know about header (e.g. for fixed-width tables where widths are spec'd in header.
        self.data.header = self.header
        self.header.data = self.data

        lines = self.inputter.get_lines(table)
        self.data.get_data_lines(lines)
        self.header.get_cols(lines)
        cols = self.header.cols
        self.data.splitter.cols = cols

        for i, str_vals in enumerate(self.data.get_str_vals()):
            if i == 0:
                n_data_cols = len(str_vals)
            if len(str_vals) != n_data_cols:
                errmsg = ('Number of header columns (%d) inconsistent with '
                          'data columns (%d) at data line %d\n'
                          'Header values: %s\n'
                          'Data values: %s' % (len(cols), len(str_vals), i,
                                               [x.name for x in cols], str_vals))
                raise InconsistentTableError(errmsg)

            for col in cols:
                col.str_vals.append(str_vals[col.index])

        self.data.set_masks(cols)

        return self.outputter(cols)

class BasicReader(BaseReader):
    """Derived reader class that reads a space-delimited table with a single
    header line at the top followed by data lines to the end of the table.
    Lines beginning with # as the first non-whitespace character are comments.
    Example::
    
      # Column definition is the first uncommented line
      # Default delimiter is the space character.
      apples oranges pears

      # Data starts after the header column definition, blank lines ignored
      1 2 3
      4 5 6
    """
    def __init__(self):
        BaseReader.__init__(self)
        self.header.splitter.delimiter = ' '
        self.data.splitter.delimiter = ' '
        self.header.start_line = 0
        self.data.start_line = 1
        self.header.comment = r'\s*#'
        self.data.comment = r'\s*#'

extra_reader_pars = ('Reader', 'Inputter', 'Outputter', 
                     'delimiter', 'comment', 'quotechar', 'header_start',
                     'data_start', 'data_end', 'converters',
                     'data_Splitter', 'header_Splitter',
                     'default_masked', 'auto_masked',
                     'names', 'include_names', 'exclude_names')

def get_reader(Reader=None, Inputter=None, Outputter=None, numpy=True, **kwargs):
    """Initialize a table reader allowing for common customizations.

    :param Reader: Reader class
    :param Inputter: Inputter class
    :param Outputter: Outputter class
    :param delimiter: column delimiter string
    :param comment: regular expression defining a comment line in table
    :param quotechar: one-character string to quote fields containing special characters
    :param header_start: line index for the header line not counting comment lines
    :param data_start: line index for the start of data not counting comment lines
    :param data_end: line index for the end of data (can be negative to count from end)
    :param converters: dict of converters
    :param data_Splitter: Splitter class to split data columns
    :param header_Splitter: Splitter class to split header columns
    :param auto_masked: use masked arrays if data reader has ``fill_values`` set
    :param default_masked: always use masked arrays
    :param names: list of names corresponding to each data column
    :param include_names: list of names to include in output (default=None selects all names)
    :param exclude_names: list of names to exlude from output (applied after ``include_names``)
    """
    if Reader is None:
        Reader = BasicReader
    reader = Reader()

    if Inputter is not None:
        reader.inputter = Inputter()

    if has_numpy and numpy:
        reader.outputter = NumpyOutputter()

    if Outputter is not None:
        reader.outputter = Outputter()

    bad_args = [x for x in kwargs if x not in extra_reader_pars]
    if bad_args:
        raise ValueError('Supplied arg(s) {0} not allowed for get_reader()'.format(bad_args))

    if 'delimiter' in kwargs:
        reader.header.splitter.delimiter = kwargs['delimiter']
        reader.data.splitter.delimiter = kwargs['delimiter']
    if 'comment' in kwargs:
        reader.header.comment = kwargs['comment']
        reader.data.comment = kwargs['comment']
    if 'quotechar' in kwargs:
        reader.header.splitter.quotechar = kwargs['quotechar']
        reader.data.splitter.quotechar = kwargs['quotechar']
    if 'data_start' in kwargs:
        reader.data.start_line = kwargs['data_start']
    if 'data_end' in kwargs:
        reader.data.end_line = kwargs['data_end']
    if 'header_start' in kwargs:
        reader.header.start_line = kwargs['header_start']
    if 'converters' in kwargs:
        reader.outputter.converters = kwargs['converters']
    if 'data_Splitter' in kwargs:
        reader.data.splitter = kwargs['data_Splitter']()
    if 'header_Splitter' in kwargs:
        reader.header.splitter = kwargs['header_Splitter']()
    if 'default_masked' in kwargs:
        reader.outputter.default_masked = kwargs['default_masked']
    if 'auto_masked' in kwargs:
        reader.outputter.auto_masked = kwargs['auto_masked']
    if 'names' in kwargs:
        reader.header.names = kwargs['names']
    if 'include_names' in kwargs:
        reader.header.include_names = kwargs['include_names']
    if 'exclude_names' in kwargs:
        reader.header.exclude_names = kwargs['exclude_names']

    return reader

def read(table, numpy=True, **kwargs):
    """Read the input ``table``.  If ``numpy`` is True (default) return the
    table in a numpy record array.  Otherwise return the table as a
    dictionary of column objects using plain python lists to hold the data.

    :param numpy: use the NumpyOutputter class else use BaseOutputter (default=True)
    :param Reader: Reader class
    :param Inputter: Inputter class
    :param Outputter: Outputter class
    :param delimiter: column delimiter string
    :param comment: regular expression defining a comment line in table
    :param quotechar: one-character string to quote fields containing special characters
    :param header_start: line index for the header line not counting comment lines
    :param data_start: line index for the start of data not counting comment lines
    :param data_end: line index for the end of data (can be negative to count from end)
    :param converters: dict of converters
    :param data_Splitter: Splitter class to split data columns
    :param header_Splitter: Splitter class to split header columns
    :param auto_masked: use masked arrays if data reader has ``fill_values`` set
    :param default_masked: always use masked arrays
    :param names: list of names corresponding to each data column
    :param include_names: list of names to include in output (default=None selects all names)
    :param exclude_names: list of names to exlude from output (applied after ``include_names``)
    """

    bad_args = [x for x in kwargs if x not in extra_reader_pars]
    if bad_args:
        raise ValueError('Supplied arg(s) {0} not allowed for get_reader()'.format(bad_args))

    # Provide a simple way to choose between the two common outputters.  If an Outputter is
    # supplied in kwargs that will take precedence.
    new_kwargs = {}
    if has_numpy and numpy:
        new_kwargs['Outputter'] = NumpyOutputter
    else:
        new_kwargs['Outputter'] = BaseOutputter
    new_kwargs.update(kwargs)
        
    reader = get_reader(**new_kwargs)
    return reader.read(table)

############################################################################################################
############################################################################################################
##
##  Custom extensions
##
############################################################################################################
############################################################################################################

class ContinuationLinesInputter(BaseInputter):
    """Inputter where lines ending in the ``continuation_char`` are joined
    with the subsequent line."""

    continuation_char = '\\'

    def process_lines(self, lines):
        striplines = (x.strip() for x in lines)
        lines = [x for x in striplines if len(x) > 0]

        parts = []
        outlines = []
        for line in lines:
            if line.endswith(self.continuation_char):
                parts.append(line.rstrip(self.continuation_char))
            else:
                parts.append(line)
                outlines.append(''.join(parts))
                parts = []

        return outlines


class NoHeaderReader(BasicReader):
    """Same as BasicReader but without a header.  Columns are autonamed using
    header.auto_format which defaults to "col%d".
    """
    def __init__(self):
        BasicReader.__init__(self)
        self.header.start_line = None
        self.data.start_line = 0

class CommentedHeader(BaseHeader):
    """Header class for which the column definition line starts with the
    comment character.  
    """
    def process_lines(self, lines):
        """Return only lines that start with the comment regexp.  For these
        lines strip out the matching characters."""
        re_comment = re.compile(self.comment)
        for line in lines:
            match = re_comment.match(line)
            if match:
                yield line[match.end():]

class CommentedHeaderReader(BaseReader):
    """Read a file where the column names are given in a line that begins with the
    header comment character, e.g.::

      # col1 col2 col3
      #  Comment...
      1 2 3
      4 5 6
    """
    def __init__(self):
        BaseReader.__init__(self)
        self.header = CommentedHeader()
        self.header.splitter.delimiter = ' '
        self.data.splitter.delimiter = ' '
        self.header.start_line = 0
        self.data.start_line = 0
        self.header.comment = r'\s*#'
        self.data.comment = r'\s*#'

class TabReader(BasicReader):
    def __init__(self):
        BasicReader.__init__(self)
        self.header.splitter.delimiter = '\t'
        self.data.splitter.delimiter = '\t'
        # Don't strip line whitespace since that includes tabs
        self.header.splitter.process_line = None  
        self.data.splitter.process_line = None

class RdbReader(TabReader):
    def __init__(self):
        TabReader.__init__(self)
        self.data.start_line = 2

class DaophotReader(BaseReader):
    """Derived reader class that reads a DAOphot file."""
    def __init__(self):
        BaseReader.__init__(self)
        self.header = DaophotHeader()
        self.inputter = ContinuationLinesInputter()
        self.data.splitter.delimiter = ' '
        self.data.start_line = 0
        self.data.comment = r'\s*#'

class DaophotHeader(BaseHeader):
    """Read the header from a file produced by the IRAF DAOphot routine.  This looks like::

         #K MERGERAD   = INDEF                   scaleunit  %-23.7g  #
         #N ID    XCENTER   YCENTER   MAG         MERR          MSKY           NITER    \
         #U ##    pixels    pixels    magnitudes  magnitudes    counts         ##       \
         #F %-9d  %-10.3f   %-10.3f   %-12.3f     %-14.3f       %-15.7g        %-6d     #
         #N         SHARPNESS   CHI         PIER  PERROR                                \
         #U         ##          ##          ##    perrors                               \
         #F         %-23.3f     %-12.3f     %-6d  %-13s                                 #
    """
    def get_cols(self, lines):
        """Initialize the header Column objects from the table ``lines`` for a DAOphot
        header.

        The DAOphot header is specialized so that we just copy the entire BaseHeader
        get_cols routine and modify as needed.  Don't bother with making it extensible.

        :param lines: list of table lines
        :returns: list of table Columns
        """
        # Get the data values from the first line of table data to determine n_data_cols
        first_data_vals = self.data.get_str_vals().next()
        n_data_cols = len(first_data_vals)

        # No column names supplied so read them from header line in table.
        self.names = []
        re_name_def = re.compile(r'#N([^#]+)#')
        for line in lines:
            if not line.startswith('#'):
                break                   # End of header lines
            else:
                match = re_name_def.search(line)
                if match:
                    self.names.extend(match.group(1).split())
        
        ##  This code is all the same as the BaseHeader get_cols()
        if len(self.names) != n_data_cols:
            errmsg = ('Number of header columns (%d) inconsistent with '
                      'data columns (%d) in first line\n'
                      'Header values: %s\n'
                      'Data values: %s' % (len(self.names), len(first_data_vals),
                                           self.names, first_data_vals))
            raise InconsistentTableError(errmsg)

        names = set(self.names)
        if self.include_names is not None:
            names.intersection_update(self.include_names)
        if self.exclude_names is not None:
            names.difference_update(self.exclude_names)
            
        self.cols = [Column(name=x, index=i) for i, x in enumerate(self.names) if x in names]

class FixedWidthSplitter(BaseSplitter):
    """Split line based on fixed start and end positions for each ``col`` in
    ``self.cols``.

    This class requires that the Header class will have defined ``col.start``
    and ``col.end`` for each column.  The reference to the ``header.cols`` gets
    put in the splitter object by the base Reader.read() function just in time
    for splitting data lines by a ``data`` object.  This class won't work for
    splitting a fixed-width header but generally the header will be used to
    determine the column start and end positions.

    Note that the ``start`` and ``end`` positions are defined in the pythonic
    style so line[start:end] is the desired substring for a column.  This splitter
    class does not have a hook for ``process_lines`` since that is generally not
    useful for fixed-width input.
    """
    def __call__(self, lines):
        for line in lines:
            vals = [line[x.start:x.end] for x in self.cols]
            if self.process_val:
                yield [self.process_val(x) for x in vals]
            else:
                yield vals


class CdsHeader(BaseHeader):
    def get_cols(self, lines):
        """Initialize the header Column objects from the table ``lines`` for a CDS
        header.  See http://vizier.u-strasbg.fr/doc/catstd/catstd-3.1.htx.

        :param lines: list of table lines
        :returns: list of table Columns
        """
        for i_col_def, line in enumerate(lines):
            if re.match(r'Byte-by-byte Description', line, re.IGNORECASE):
                break

        re_col_def = re.compile(r"""\s*
                                    (?P<start> \d+ \s* -)? \s+
                                    (?P<end>   \d+)        \s+
                                    (?P<format> [\w.]+)     \s+
                                    (?P<units> \S+)        \s+
                                    (?P<name>  \S+)        \s+
                                    (?P<descr> \S.+)""",
                                re.VERBOSE)

        cols = []
        for i, line in enumerate(itertools.islice(lines, i_col_def+4, None)):
            if line.startswith('------') or line.startswith('======='):
                break
            match = re_col_def.match(line)
            if match:
                col = Column(name=match.group('name'), index=i)
                col.start = int(re.sub(r'[-\s]', '', match.group('start') or match.group('end'))) - 1
                col.end = int(match.group('end'))
                col.units = match.group('units')
                col.descr = match.group('descr')
                cols.append(col)
            else:  # could be a continuation of the previous col's description
                if cols:
                    cols[-1].descr += line.strip()
                else:
                    raise ValueError('Line "%s" not parsable as CDS header' % line)

        self.names = [x.name for x in cols]
        names = set(self.names)
        if self.include_names is not None:
            names.intersection_update(self.include_names)
        if self.exclude_names is not None:
            names.difference_update(self.exclude_names)
            
        self.cols = [x for x in cols if x.name in names]

        # Re-index the cols because the FixedWidthSplitter does NOT return the ignored
        # cols (as is in the case for typical delimiter-based splitters)
        for i, col in enumerate(self.cols):
            col.index = i
            

class CdsData(BaseData):
    """CDS table data reader
    """
    splitter_class = FixedWidthSplitter
    
    def process_lines(self, lines):
        """Skip over CDS header by finding the last section delimiter"""
        i_sections = [i for (i, x) in enumerate(lines)
                      if x.startswith('------') or x.startswith('=======')]
        return lines[i_sections[-1]+1 : ]


class CdsReader(BaseReader):
    """Read a CDS format file.

    See http://vizier.u-strasbg.fr/doc/catstd/catstd-3.1.htx or the test file
    t/cds.dat"""
    def __init__(self):
        BaseReader.__init__(self)
        self.header = CdsHeader()
        self.data = CdsData()

