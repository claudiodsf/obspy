import copy
from future.utils import native_str as nstr
import time

import numpy as np
from obspy.core.compatibility import is_bytes_buffer
from obspy.core import Stream, Trace, Stats, UTCDateTime
from obspy.io.rg16.util import _read


def _read_rg16(filename, headonly=False, starttime=None, endtime=None,
               merge=False, contacts_north=False, details=False):
    """
    Read Fairfield Nodal's Receiver Gather File Format version 1.6-1.
    :param filename: a path to the file to read or a file object.
    :type filename: str, buffer
    :param headonly: If True don't read data, only main informations
     contained in the headers of the trace block are read.
    :type headonly: optional, bool
    :param starttime: If not None dont read traces that start before starttime.
    :type starttime: optional, obspy.UTCDateTime
    :param endtime: If not None dont read traces that start after endtime.
    :type endtime: optional, obspy.UTCDateTime
    :param merge: If True merge contiguous data blocks as they are found. For
     continuous data files having 100,000+ traces this will create
     more manageable streams.
    :type merge: bool
    :param contacts_north: If this parameter is set to True, it will map the
    components to Z (1C, 3C), N (3C), and E (3C) as well as correct
    the polarity for the vertical component.
    :type contacts_north: bool
    :param details: If True, all the informations contained in the headers
     are read).
    :type details: optional, bool
    :return: An ObsPy :class:`~obspy.core.stream.Stream` object.
    Frequencies are expressed in hertz and time is expressed in seconds
    (except for date).
    """
    if starttime is None:
        starttime = UTCDateTime(1970, 1, 1)
    if endtime is None:
        local_time = time.localtime()
        endtime = UTCDateTime(local_time[0], local_time[1], local_time[2],
                              local_time[3], local_time[4], local_time[5])
    if is_bytes_buffer(filename):
        return _internal_read_rg16(filename, headonly, starttime, endtime,
                                   merge, contacts_north, details)
    else:
        with open(filename, 'rb') as fi:
            return _internal_read_rg16(fi, headonly, starttime, endtime,
                                       merge, contacts_north, details)


def _internal_read_rg16(fi, headonly, starttime, endtime, merge,
                        contacts_north, details):
    """
    Read Fairfield Nodal's Receiver Gather File Format version 1.6-1.
    :param fi: a file object.
    :type fi: buffer
    :param headonly: If True don't read data, only main informations
     contained in the headers of the trace block are read.
    :type headonly: optional, bool
    :param starttime: If not None dont read traces that start before starttime.
    :type starttime: optional, obspy.UTCDateTime
    :param endtime: If not None dont read traces that start after endtime.
    :type endtime: optional, obspy.UTCDateTime
    :param merge: If True merge contiguous data blocks as they are found. For
     continuous data files having 100,000+ traces this will create
     more manageable streams.
    :type merge: bool
    :param contacts_north: If this parameter is set to True, it will map the
    components to Z (1C, 3C), N (3C), and E (3C) as well as correct
    the polarity for the vertical component.
    :type contacts_north: bool
    :param details: If True, all the informations contained in the headers
     are read).
    :type details: optional, bool
    :return: An ObsPy :class:`~obspy.core.stream.Stream` object.
    Frequencies are expressed in hertz and time is expressed in seconds
    (except for date).
    """
    (nbr_channel_set_headers, nbr_extended_headers,
     nbr_external_headers) = _cmp_nbr_headers(fi)
    nbr_records = _cmp_nbr_records(fi)
    trace_block_start = 32 * (2 + nbr_channel_set_headers +
                              nbr_extended_headers + nbr_external_headers)
    traces = []  # list to store traces
    for i in range(0, nbr_records):
        nbr_bytes_trace_block = _cmp_jump(fi, trace_block_start)
        starttime_block = _read(fi, trace_block_start + 20 + 2*32, 8,
                                'binary') / 1e6
        if starttime.timestamp <= starttime_block and\
           starttime_block < endtime.timestamp:
            trace = _make_trace(fi, trace_block_start, headonly,
                                contacts_north, details)
            traces.append(trace)
        trace_block_start += nbr_bytes_trace_block
    if merge:
        traces = _quick_merge(traces)
    return Stream(traces=traces)


def _cmp_nbr_headers(fi):
    """
    Return a tuple containing the number of channel set headers,
    the number of extended headers and the number of external headers
    in the file.
    """
    nbr_channel_set_headers = _read(fi, 28, 1, 'bcd')
    nbr_extended_headers = _read(fi, 37, 2, 'binary')
    nbr_external_headers = _read(fi, 39, 3, 'binary')
    return (nbr_channel_set_headers, nbr_extended_headers,
            nbr_external_headers)


def _cmp_nbr_records(fi):
    """
    Return the number of records in the file (ie number of time slices
    multiplied by the number of components).
    """
    initial_header = _read_initial_headers(fi)
    channel_sets_descriptor = initial_header['channel_sets_descriptor']
    channels_number = set()
    for _, val in channel_sets_descriptor.items():
        channels_number.add(val['RU_channel_number'])
    nbr_component = len(channels_number)
    extended_header_2 = initial_header['extended_headers']['2']
    nbr_time_slices = extended_header_2['nbr_time_slices']
    nbr_records = nbr_time_slices * nbr_component
    return nbr_records


def _cmp_jump(fi, trace_block_start):
    """
    Return the number of bytes in a trace block.
    """
    nbr_trace_extension_block = _read(fi, trace_block_start + 9, 1, 'binary')
    nbr_bytes_header_trace = 20 + 32 * nbr_trace_extension_block
    nbr_sample_trace = _read(fi, trace_block_start + 27, 3, 'binary')
    nbr_bytes_trace_data = nbr_sample_trace * 4
    nbr_bytes_trace_block = nbr_bytes_trace_data + nbr_bytes_header_trace
    return nbr_bytes_trace_block


def _make_trace(fi, trace_block_start, headonly, standard_orientation,
                details):
    """
    Make obspy trace from a trace block (header + trace)
    """
    stats = _make_stats(fi, trace_block_start, standard_orientation, details)
    if headonly:
        data = np.array([])
    else:  # read trace
        nbr_trace_extension_block = _read(fi, trace_block_start + 9,
                                          1, 'binary')
        trace_start = trace_block_start + 20 + nbr_trace_extension_block * 32
        nbr_sample_trace = _read(fi, trace_block_start + 27, 3, 'binary')
        nbr_bytes_trace = 4 * nbr_sample_trace
        data = _read(fi, trace_start, nbr_bytes_trace, 'IEEE')
        if stats.channel[-1] == 'Z':
            data = -data
            data = data.astype('>f4')
    return Trace(data=data, header=stats)


def _make_stats(fi, trace_block_start, standard_orientation, details):
    """
    Make Stats object from information contained in the header of the trace.
    """
    base_scan_interval = _read(fi, 22, 1, 'binary')
    sampling_rate = int(1000 / (base_scan_interval / 16))
    # map sampling rate to band code according to seed standard
    band_map = {2000: 'G', 1000: 'G', 500: 'D', 250: 'D'}
    # geophone instrument code
    instrument_code = 'P'
    # mapping for "standard_orientation"
    standard_component_map = {'2': 'Z', '3': 'N', '4': 'E'}
    component = str(_read(fi, trace_block_start + 40, 1, 'binary'))
    if standard_orientation:
        component = standard_component_map[component]
    chan = band_map[sampling_rate] + instrument_code + component
    npts = _read(fi, trace_block_start + 27, 3, 'binary')
    start_time = _read(fi, trace_block_start + 20 + 2*32, 8, 'binary') / 1e6
    end_time = start_time + (npts - 1) * (1/sampling_rate)
    network = _read(fi, trace_block_start + 20, 3, 'binary')
    station = _read(fi, trace_block_start + 23, 3, 'binary')
    location = _read(fi, trace_block_start + 26, 1, 'binary')
    statsdict = dict(starttime=UTCDateTime(start_time),
                     endtime=UTCDateTime(end_time),
                     sampling_rate=sampling_rate,
                     npts=npts,
                     network=str(network),
                     station=str(station),
                     location=str(location),
                     channel=chan)
    if details:
        statsdict['rg16'] = {}
        statsdict['rg16']['initial_headers'] = {}
        stats_initial_headers = statsdict['rg16']['initial_headers']
        stats_initial_headers.update(_read_initial_headers(fi))
        statsdict['rg16']['trace_headers'] = {}
        stats_tr_headers = statsdict['rg16']['trace_headers']
        stats_tr_headers.update(_read_trace_header(fi, trace_block_start))
        nbr_tr_header_block = _read(fi, trace_block_start + 9,
                                    1, 'binary')
        if nbr_tr_header_block > 0:
            stats_tr_headers.update(_read_trace_headers(fi,
                                                        trace_block_start,
                                                        nbr_tr_header_block))
    return Stats(statsdict)


def _quick_merge(traces, small_number=.000001):
    """
    Specialized function for merging traces produced by _read_rg16.
    Requires that traces are of the same datatype, have the same
    sampling_rate, and dont have data overlaps.
    :param traces: list of ObsPy :class:`~obspy.core.trace.Trace` objects.
    :param small_number:
        A small number for determining if traces should be merged. Should be
        much less than one sample spacing.
    :return: list of ObsPy :class:`~obspy.core.trace.Trace` objects.
    """
    # make sure sampling rates are all the same
    assert len({tr.stats.sampling_rate for tr in traces}) == 1
    assert len({tr.data.dtype for tr in traces}) == 1
    sampling_rate = traces[0].stats.sampling_rate
    diff = 1. / sampling_rate + small_number
    # get the array
    ar, trace_ar = _trace_list_to_rec_array(traces)
    # get groups of traces that can be merged together
    group = _get_trace_groups(ar, diff)
    group_numbers = np.unique(group)
    out = [None] * len(group_numbers)  # init output list
    for index, gnum in enumerate(group_numbers):
        trace_ar_to_merge = trace_ar[group == gnum]
        new_data = np.concatenate(list(trace_ar_to_merge['data']))
        # get updated stats object
        new_stats = copy.deepcopy(trace_ar_to_merge['stats'][0])
        new_stats.npts = len(new_data)
        out[index] = Trace(data=new_data, header=new_stats)
    return out


def _trace_list_to_rec_array(traces):
    """
    return a recarray from the trace list. These are seperated into
    two arrays due to a weird issue with numpy.sort returning and error
    set.
    """
    # get the id, starttime, endtime into a recarray
    # rec array column names must be native strings due to numpy issue 2407
    dtype1 = [(nstr('id'), np.object), (nstr('starttime'), float),
              (nstr('endtime'), float)]
    dtype2 = [(nstr('data'), np.object), (nstr('stats'), np.object)]
    data1 = [(tr.id, tr.stats.starttime.timestamp, tr.stats.endtime.timestamp)
             for tr in traces]
    data2 = [(tr.data, tr.stats) for tr in traces]
    ar1 = np.array(data1, dtype=dtype1)  # array of id, starttime, endtime
    ar2 = np.array(data2, dtype=dtype2)  # array of data, stats objects
    #
    sort_index = np.argsort(ar1, order=['id', 'starttime'])
    return ar1[sort_index], ar2[sort_index]


def _get_trace_groups(ar, diff):
    """
    Return an array of ints where each element corresponds to a pre-merged
    trace row. All trace rows with the same group number can be merged.
    """
    # get a bool of if ids are the same as the next row down
    ids_different = np.ones(len(ar), dtype=bool)
    ids_different[1:] = ar['id'][1:] != ar['id'][:-1]
    # get bool of endtimes within one sample of starttime of next row
    disjoint = np.zeros(len(ar), dtype=bool)
    start_end_diffs = ar['starttime'][1:] - ar['endtime'][:-1]
    disjoint[:-1] = np.abs(start_end_diffs) <= diff
    # get groups (not disjoint, not different ids)
    return np.cumsum(ids_different & disjoint)


def _read_trace_headers(fi, trace_block_start, nbr_trace_header):
    """
    Read headers in the trace block.
    """
    trace_headers = {}
    dict_func = {'1': _read_trace_header_1, '2': _read_trace_header_2,
                 '3': _read_trace_header_3, '4': _read_trace_header_4,
                 '5': _read_trace_header_5, '6': _read_trace_header_6,
                 '7': _read_trace_header_7, '8': _read_trace_header_8,
                 '9': _read_trace_header_9, '10': _read_trace_header_10}
    for i in range(1, nbr_trace_header + 1):
        trace_headers.update(dict_func[str(i)](fi, trace_block_start))
    return trace_headers


def _read_trace_header(fi, trace_block_start):
    """
    Read the 20 bytes trace header
    (first header in the trace block).
    """
    trace_number = _read(fi, trace_block_start + 4, 2, 'bcd')
    trace_edit_code = _read(fi, trace_block_start + 11, 1, 'binary')
    return {'trace_number': trace_number, 'trace_edit_code': trace_edit_code}


def _read_trace_header_1(fi, trace_block_start):
    """
    Read trace header 1
    """
    pos = trace_block_start + 20
    extended_receiver_line_nbr = _read(fi, pos + 10, 5, 'binary')
    ext_receiver_point_nbr = _read(fi, pos + 15, 5, 'binary')
    sensor_type = _read(fi, pos + 20, 1, 'binary')
    trace_count_file = _read(fi, pos + 21, 4, 'binary')
    dict_header_1 = {'extended_receiver_line_nbr': extended_receiver_line_nbr,
                     'extended_receiver_point_nbr': ext_receiver_point_nbr,
                     'sensor_type': sensor_type,
                     'trace_count_file': trace_count_file}
    return dict_header_1


def _read_trace_header_2(fi, trace_block_start):
    """
    Read trace header 2
    """
    pos = trace_block_start + 20 + 32
    shot_line_nbr = _read(fi, pos, 4, 'binary')
    shot_point = _read(fi, pos + 4, 4, 'binary')
    shot_point_index = _read(fi, pos + 8, 1, 'binary')
    shot_point_pre_plan_x = _read(fi, pos + 9, 4, 'binary') / 10
    shot_point_pre_plan_y = _read(fi, pos + 13, 4, 'binary') / 10
    shot_point_final_x = _read(fi, pos + 17, 4, 'binary') / 10
    shot_point_final_y = _read(fi, pos + 21, 4, 'binary') / 10
    shot_point_final_depth = _read(fi, pos + 25, 4, 'binary') / 10
    leg_source_info = {'0': 'undefined', '1': 'preplan', '2': 'as shot',
                       '3': 'post processed'}
    key = str(_read(fi, pos + 29, 1, 'binary'))
    source_of_final_shot_info = leg_source_info[key]
    leg_energy_source = {'0': 'undefined', '1': 'vibroseis', '2': 'dynamite',
                         '3': 'air gun'}
    key = str(_read(fi, pos + 30, 1, 'binary'))
    energy_source_type = leg_energy_source[key]
    dict_header_2 = {'shot_line_nbr': shot_line_nbr, 'shot_point': shot_point,
                     'shot_point_index': shot_point_index,
                     'shot_point_pre_plan_x': shot_point_pre_plan_x,
                     'shot_point_pre_plan_y': shot_point_pre_plan_y,
                     'shot_point_final_x': shot_point_final_x,
                     'shot_point_final_y': shot_point_final_y,
                     'shot_point_final_depth': shot_point_final_depth,
                     'source_of_final_shot_info': source_of_final_shot_info,
                     'energy_source_type': energy_source_type}
    return dict_header_2


def _read_trace_header_3(fi, trace_block_start):
    """
    Read trace header 3
    """
    pos = trace_block_start + 20 + 32 * 2
    epoch_time = UTCDateTime(_read(fi, pos, 8, 'binary') / 1e6)
    # shot skew time in second
    shot_skew_time = _read(fi, pos + 8, 8, 'binary') / 1e6
    # time shift clock correction in second
    time_shift_clock_correc = _read(fi, pos + 16, 8, 'binary') / 1e9
    # remaining clock correction in second
    remaining_clock_correction = _read(fi, pos + 24, 8, 'binary') / 1e9
    dict_header_3 = {'epoch_time': epoch_time,
                     'shot_skew_time': shot_skew_time,
                     'time_shift_clock_correction': time_shift_clock_correc,
                     'remaining_clock_correction': remaining_clock_correction}
    return dict_header_3


def _read_trace_header_4(fi, trace_block_start):
    """
    Read trace header 4
    """
    pos = trace_block_start + 20 + 32 * 3
    # pre shot guard band in second
    pre_shot_guard_band = _read(fi, pos, 4, 'binary') / 1e3
    # post shot guard band in second
    post_shot_guard_band = _read(fi, pos + 4, 4, 'binary') / 1e3
    # Preamp gain in dB
    preamp_gain = _read(fi, pos + 8, 1, 'binary')
    leg_trace_clipped = {'0': 'not clipped', '1': 'digital clip detected',
                         '2': 'analog clip detected'}
    key = str(_read(fi, pos + 9, 1, 'binary'))
    trace_clipped_flag = leg_trace_clipped[key]
    leg_record_type = {'2': 'test data record',
                       '8': 'normal seismic data record'}
    key = str(_read(fi, pos + 10, 1, 'binary'))
    record_type_code = leg_record_type[key]
    leg_shot_flag = {'0': 'normal', '1': 'bad-operator specified',
                     '2': 'bad-failed to QC test'}
    key = str(_read(fi, pos + 11, 1, 'binary'))
    shot_status_flag = leg_shot_flag[key]
    external_shot_id = _read(fi, pos + 12, 4, 'binary')
    first_break_pick = _read(fi, pos + 24, 4, 'IEEE')
    post_processed_rms_noise = _read(fi, pos + 28, 4, 'IEEE')
    dict_header_4 = {'pre_shot_guard_band': pre_shot_guard_band,
                     'post_shot_guard_band': post_shot_guard_band,
                     'preamp_gain': preamp_gain,
                     'trace_clipped_flag': trace_clipped_flag,
                     'record_type_code': record_type_code,
                     'shot_status_flag': shot_status_flag,
                     'external_shot_id': external_shot_id,
                     'post_processed_first_break_pick_time': first_break_pick,
                     'post_processed_rms_noise': post_processed_rms_noise}
    return dict_header_4


def _read_trace_header_5(fi, trace_block_start):
    """
    Read trace header 5
    """
    pos = trace_block_start + 20 + 32 * 4
    receiver_point_pre_plan_x = _read(fi, pos + 9, 4, 'binary') / 10
    receiver_point_pre_plan_y = _read(fi, pos + 13, 4, 'binary') / 10
    receiver_point_final_x = _read(fi, pos + 17, 4, 'binary') / 10
    receiver_point_final_y = _read(fi, pos + 21, 4, 'binary') / 10
    receiver_point_final_depth = _read(fi, pos + 25, 4, 'binary') / 10
    leg_source_receiver_info = {'1': 'preplan',
                                '2': 'as laid (no navigation sensor)',
                                '3': 'as laid (HiPAP only)',
                                '4': 'as laid (HiPAP and INS)',
                                '5': 'as laid (HiPAP and DVL)',
                                '6': 'as laid (HiPAP, DVL and INS)',
                                '7': 'post processed (HiPAP only)',
                                '8': 'post processed (HiPAP and INS)',
                                '9': 'post processed (HiPAP and DVL)',
                                '10': 'post processed (HiPAP, DVL ans INS)',
                                '11': 'first break analysis'}
    key = str(_read(fi, pos + 29, 1, 'binary'))
    source_receiver_info = leg_source_receiver_info[key]
    dict_header_5 = {'receiver_point_pre_plan_x': receiver_point_pre_plan_x,
                     'receiver_point_pre_plan_y': receiver_point_pre_plan_y,
                     'receiver_point_final_x': receiver_point_final_x,
                     'receiver_point_final_y': receiver_point_final_y,
                     'receiver_point_final_depth': receiver_point_final_depth,
                     'source_of_final_receiver_info': source_receiver_info}
    return dict_header_5


def _read_trace_header_6(fi, trace_block_start):
    """
    Read trace header 6
    """
    pos = trace_block_start + 20 + 32 * 5
    tilt_matrix_h1x = _read(fi, pos, 4, 'IEEE')
    tilt_matrix_h2x = _read(fi, pos + 4, 4, 'IEEE')
    tilt_matrix_vx = _read(fi, pos + 8, 4, 'IEEE')
    tilt_matrix_h1y = _read(fi, pos + 12, 4, 'IEEE')
    tilt_matrix_h2y = _read(fi, pos + 16, 4, 'IEEE')
    tilt_matrix_vy = _read(fi, pos + 20, 4, 'IEEE')
    tilt_matrix_h1z = _read(fi, pos + 24, 4, 'IEEE')
    tilt_matrix_h2z = _read(fi, pos + 28, 4, 'IEEE')
    dict_header_6 = {'tilt_matrix_h1x': tilt_matrix_h1x,
                     'tilt_matrix_h2x': tilt_matrix_h2x,
                     'tilt_matrix_vx': tilt_matrix_vx,
                     'tilt_matrix_h1y': tilt_matrix_h1y,
                     'tilt_matrix_h2y': tilt_matrix_h2y,
                     'tilt_matrix_vy': tilt_matrix_vy,
                     'tilt_matrix_h1z': tilt_matrix_h1z,
                     'tilt_matrix_h2z': tilt_matrix_h2z}
    return dict_header_6


def _read_trace_header_7(fi, trace_block_start):
    """
    Read trace header 7
    """
    pos = trace_block_start + 20 + 32 * 6
    tilt_matrix_vz = _read(fi, pos, 4, 'IEEE')
    azimuth_degree = _read(fi, pos + 4, 4, 'IEEE')
    pitch_degree = _read(fi, pos + 8, 4, 'IEEE')
    roll_degree = _read(fi, pos + 12, 4, 'IEEE')
    remote_unit_temp = _read(fi, pos + 16, 4, 'IEEE')
    remote_unit_humidity = _read(fi, pos + 20, 4, 'IEEE')
    orientation_matrix = _read(fi, pos + 24, 4, 'binary')
    gimbal_corrections = _read(fi, pos + 28, 1, 'binary')
    dict_header_7 = {'tilt_matrix_vz': tilt_matrix_vz,
                     'azimuth_degree': azimuth_degree,
                     'pitch_degree': pitch_degree,
                     'roll_degree': roll_degree,
                     'remote_unit_temp': remote_unit_temp,
                     'remote_unit_humidity': remote_unit_humidity,
                     'orientation_matrix_version_nbr': orientation_matrix,
                     'gimbal_corrections': gimbal_corrections}
    return dict_header_7


def _read_trace_header_8(fi, trace_block_start):
    """
    Read trace header 8
    """
    pos = trace_block_start + 20 + 32 * 7
    fairfield_test = _read(fi, pos, 4, 'binary')
    first_test = _read(fi, pos + 4, 4, 'binary')
    second_test = _read(fi, pos + 8, 4, 'binary')
    # start delay in second
    start_delay = _read(fi, pos + 12, 4, 'binary') / 1e6
    dc_filter_flag = _read(fi, pos + 16, 4, 'binary')
    dc_filter_frequency = _read(fi, pos + 20, 4, 'IEEE')
    leg_preamp_path = {'0': 'external input selected',
                       '1': 'simulated data selected',
                       '2': 'pre-amp input shorted to ground',
                       '3': 'test oscillator with sensors',
                       '4': 'test oscillator without sensors',
                       '5': 'common mode test oscillator with sensors',
                       '6': 'common mode test oscillator without sensors',
                       '7': 'test oscillator on positive sensors with neg\
                             sensor grounded',
                       '8': 'test oscillator on negative sensors with pos\
                             sensor grounded',
                       '9': 'test oscillator on positive PA input with neg\
                             PA input ground',
                       '10': 'test oscillator on negative PA input, with pos\
                              PA input ground',
                       '11': 'test oscillator on positive PA input, with neg\
                              PA input ground, no sensors',
                       '12': 'test oscillator on negative PA input, with pos\
                              PA input ground, no sensors'}
    key = str(_read(fi, pos + 24, 4, 'binary'))
    preamp_path = leg_preamp_path[key]
    leg_test_oscillator = {'0': 'test oscillator path open',
                           '1': 'test signal selected',
                           '2': 'DC reference selected',
                           '3': 'test oscillator path grounded',
                           '4': 'DC reference toggle selected'}
    key = str(_read(fi, pos + 28, 4, 'binary'))
    test_oscillator = leg_test_oscillator[key]
    dict_header_8 = {'fairfield_test_analysis_code': fairfield_test,
                     'first_test_oscillator_attenuation': first_test,
                     'second_test_oscillator_attenuation': second_test,
                     'start_delay': start_delay,
                     'dc_filter_flag': dc_filter_flag,
                     'dc_filter_frequency': dc_filter_frequency,
                     'preamp_path': preamp_path,
                     'test_oscillator_signal_type': test_oscillator}
    return dict_header_8


def _read_trace_header_9(fi, trace_block_start):
    """
    Read trace header 9
    """
    pos = trace_block_start + 20 + 32 * 8
    leg_signal_type = {'0': 'pattern is address ramp',
                       '1': 'pattern is RU address ramp',
                       '2': 'pattern is built from provided values',
                       '3': 'pattern is random numbers',
                       '4': 'pattern is a walking 1s',
                       '5': 'pattern is a walking 0s',
                       '6': 'test signal is a specified DC value',
                       '7': 'test signal is a pulse train with\
                             specified duty cycle',
                       '8': 'test signal is a sine wave',
                       '9': 'test signal is a dual tone sine',
                       '10': 'test signal is an impulse',
                       '11': 'test signal is a step function'}
    key = str(_read(fi, pos, 4, 'binary'))
    test_signal_type = leg_signal_type[key]
    # test signal generator frequency 1 in hertz
    test_signal_freq_1 = _read(fi, pos + 4, 4, 'binary') / 1e3
    # test signal generator frequency 2 in hertz
    test_signal_freq_2 = _read(fi, pos + 8, 4, 'binary') / 1e3
    # test signal generator amplitude 1 in dB down from full scale -120 to 120
    test_signal_amp_1 = _read(fi, pos + 12, 4, 'binary')
    # test signal generator amplitude 2 in dB down from full scale -120 to 120
    test_signal_amp_2 = _read(fi, pos + 16, 4, 'binary')
    # test signal generator duty cycle in percentage
    duty_cycle = _read(fi, pos + 20, 4, 'IEEE')
    # test signal generator active duration in second
    active_duration = _read(fi, pos + 24, 4, 'binary') / 1e6
    # test signal generator activation time in second
    activation_time = _read(fi, pos + 28, 4, 'binary') / 1e6
    dict_header_9 = {'test_signal_generator_signal_type': test_signal_type,
                     'test_signal_generator_frequency_1': test_signal_freq_1,
                     'test_signal_generator_frequency_2': test_signal_freq_2,
                     'test_signal_generator_amplitude_1': test_signal_amp_1,
                     'test_signal_generator_amplitude_2': test_signal_amp_2,
                     'test_signal_generator_duty_cycle_percentage': duty_cycle,
                     'test_signal_generator_active_duration': active_duration,
                     'test_signal_generator_activation_time': activation_time}
    return dict_header_9


def _read_trace_header_10(fi, trace_block_start):
    """
    Read trace header 10
    """
    pos = trace_block_start + 20 + 32 * 9
    idle_level = _read(fi, pos, 4, 'binary')
    active_level = _read(fi, pos + 4, 4, 'binary')
    pattern_1 = _read(fi, pos + 8, 4, 'binary')
    pattern_2 = _read(fi, pos + 12, 4, 'binary')
    dict_header_10 = {'test_signal_generator_idle_level': idle_level,
                      'test_signal_generator_active_level': active_level,
                      'test_signal_generator_pattern_1': pattern_1,
                      'test_signal_generator_pattern_2': pattern_2}
    return dict_header_10


def _is_rg16(filename):
    """
    Determine if a file is a rg16 file or not.
    :param filename: a path to the file to check or a rg16 file object.
    :type filename: str, buffer.
    :rtype: bool
    :return: True if the file is a rg16 file.
    """
    if is_bytes_buffer(filename):
        return _internal_is_rg16(filename)
    else:
        with open(filename, "rb") as fi:
            return _internal_is_rg16(fi)


def _internal_is_rg16(fi):
    """
    Determine if a file object fi is a rg16 file.
    :param fi: a file object.
    :type fi: buffer
    :rtype: bool
    :return: True if the file object is a rg16 file.
    """
    try:
        sample_format = _read(fi, 2, 2, 'bcd')
        manufacturer_code = _read(fi, 16, 1, 'bcd')
        version = _read(fi, 42, 2, 'binary')
    except ValueError:  # if file too small
        return False
    con1 = version == 262 and sample_format == 8058
    return con1 and manufacturer_code == 20


def _read_initial_headers(filename):
    """
    Extract all the informations contained in the headers located before data,
    at the beginning of the rg16 file.
    :param filename : a path to the rg16 file or a rg16 file object.
    :type filename: str, buffer
    :return: a dictionnary containing all the informations
             in the initial headers
    Frequencies are expressed in hertz and time is expressed in seconds
    (except for the date).
    """
    if is_bytes_buffer(filename):
        return _internal_read_initial_headers(filename)
    else:
        with open(filename, 'rb') as fi:
            return _internal_read_initial_headers(fi)


def _internal_read_initial_headers(fi):
    """
    Extract all the informations contained in the headers located before data,
    at the beginning of the rg16 file object.
    :param fi : a rg16 file object.
    :type fi: buffer
    :return: a dictionnary containing all the informations
             in the initial headers
    Frequencies are expressed in hertz and time is expressed in seconds
    (except for the date).
    """
    headers_content = {}
    headers_content['general_header_1'] = _read_general_header_1(fi)
    headers_content['general_header_2'] = _read_general_header_2(fi)
    headers_content['channel_sets_descriptor'] = _read_channel_sets(fi)
    headers_content['extended_headers'] = _read_extended_headers(fi)
    return headers_content


def _read_general_header_1(fi):
    """
    Extract informations contained in the general header block 1
    """
    gen_head_1 = {}
    gen_head_1['file_number'] = _read(fi, 0, 2, 'bcd')
    gen_head_1['sample_format_code'] = _read(fi, 2, 2, 'bcd')
    gen_head_1['general_constant'] = _read(fi, 4, 6, 'bcd')
    gen_head_1['time_slice_year'] = _read(fi, 10, 1, 'bcd')
    gen_head_1['nbr_add_general_header'] = _read(fi, 11, 0.5, 'bcd')
    gen_head_1['julian_day'] = _read(fi, 11, 1.5, 'bcd', False)
    gen_head_1['time_slice'] = _read(fi, 13, 3, 'bcd')
    gen_head_1['manufacturer_code'] = _read(fi, 16, 1, 'bcd')
    gen_head_1['manufacturer_serial_number'] = _read(fi, 17, 2, 'bcd')
    gen_head_1['base_scan_interval'] = _read(fi, 22, 1, 'binary')
    gen_head_1['polarity_code'] = _read(fi, 23, 0.5, 'binary')
    gen_head_1['record_type'] = _read(fi, 25, 0.5, 'binary')
    gen_head_1['scan_type_per_record'] = _read(fi, 27, 1, 'bcd')
    gen_head_1['nbr_channel_set'] = _read(fi, 28, 1, 'bcd')
    gen_head_1['nbr_skew_block'] = _read(fi, 29, 1, 'bcd')
    return gen_head_1


def _read_general_header_2(fi):
    """
    Extract informations contained in the general header block 2
    """
    gen_head_2 = {}
    gen_head_2['extended_file_number'] = _read(fi, 32, 3, 'binary')
    gen_head_2['extended_channel_sets_per_scan_type'] = _read(fi, 35, 2,
                                                              'binary')
    gen_head_2['extended_header_blocks'] = _read(fi, 37, 2, 'binary')
    gen_head_2['external_header_blocks'] = _read(fi, 39, 3, 'binary')
    gen_head_2['version_number'] = _read(fi, 42, 2, 'binary')
    gen_head_2['extended_record_length'] = _read(fi, 46, 3, 'binary')
    gen_head_2['general_header_block_number'] = _read(fi, 50, 1, 'binary')
    return gen_head_2


def _read_channel_sets(fi):
    """
    Extract informations of all channel set descriptor blocks.
    """
    channel_sets = {}
    nbr_channel_set = _read(fi, 28, 1, 'bcd')
    start_byte = 64
    for i in range(0, nbr_channel_set):
        channel_set_name = str(i+1)
        channel_sets[channel_set_name] = _read_channel_set(fi, start_byte)
        start_byte += 32
    return channel_sets


def _read_channel_set(fi, start_byte):
    """
    Extract informations contained in the ith channel set descriptor.
    """
    channel_set = {}
    channel_set['scan_type_number'] = _read(fi, start_byte, 1, 'bcd')
    channel_set['channel_set_number'] = _read(fi, start_byte + 1, 1, 'bcd')
    channel_set['channel_set_start_time'] = _read(fi, start_byte + 2,
                                                  2, 'binary') * 2e-3
    channel_set['channel_set_end_time'] = _read(fi, start_byte + 4, 2,
                                                'binary') * 2e-3
    channel_set['optionnal_MP_factor'] = _read(fi, start_byte + 6, 1, 'binary')
    channel_set['mp_factor_descaler_multiplier'] = _read(fi, start_byte + 7,
                                                         1, 'binary')
    channel_set['nbr_channels_in_channel_set'] = _read(fi, start_byte + 8,
                                                       2, 'bcd')
    channel_set['channel_type_code'] = _read(fi, start_byte + 10, 0.5,
                                             'binary')
    channel_set['nbr_sub_scans'] = _read(fi, start_byte + 11, 0.5, 'bcd')
    channel_set['gain_control_type'] = _read(fi, start_byte + 11,
                                             0.5, 'bcd', False)
    # alias filter frequency in Hertz
    channel_set['alias_filter_frequency'] = _read(fi, start_byte + 12,
                                                  2, 'bcd')
    # alias filter slope in dB per octave
    channel_set['alias_filter_slope'] = _read(fi, start_byte + 14, 2, 'bcd')
    # low cut filter frequency in hertz
    channel_set['low_cut_filter_freq'] = _read(fi, start_byte + 16, 2, 'bcd')
    # low cut filter slope in dB per octave
    channel_set['low_cut_filter_slope'] = _read(fi, start_byte + 18, 2, 'bcd')
    # notch filter frequency in Hertz
    notch_filter_freq = _read(fi, start_byte + 20, 2, 'bcd') / 10
    channel_set['notch_filter_freq'] = notch_filter_freq
    # second notch filter frequency in Hertz
    notch_2_filter_freq = _read(fi, start_byte + 22, 2, 'bcd') / 10
    channel_set['notch_2_filter_freq'] = notch_2_filter_freq
    # third notch filter frequency in Hertz
    notch_3_filter_freq = _read(fi, start_byte + 24, 2, 'bcd') / 10
    channel_set['notch_3_filter_freq'] = notch_3_filter_freq
    channel_set['extended_channel_set_number'] = _read(fi, start_byte + 26, 2,
                                                       'binary')
    channel_set['extended_header_flag'] = _read(fi, start_byte + 28, 0.5,
                                                'binary')
    channel_set['nbr_32_byte_trace_header_extension'] = _read(fi,
                                                              start_byte + 28,
                                                              0.5, 'binary',
                                                              False)
    channel_set['vertical_stack_size'] = _read(fi, start_byte + 29, 1,
                                               'binary')
    channel_set['RU_channel_number'] = _read(fi, start_byte + 30, 1, 'binary')
    channel_set['array_forming'] = _read(fi, start_byte + 31, 1, 'binary')
    return channel_set


def _read_extended_headers(fi):
    """
    Extract informations from the extended headers.
    """
    extended_headers = {}
    nbr_channel_set = _read(fi, 28, 1, 'bcd')
    start_byte = 32 + 32 + 32*nbr_channel_set
    extended_headers['1'] = _read_extended_header_1(fi, start_byte)
    start_byte += 32
    extended_headers['2'] = _read_extended_header_2(fi, start_byte)
    start_byte += 32
    extended_headers['3'] = _read_extended_header_3(fi, start_byte)
    nbr_extended_headers = _read(fi, 37, 2, 'binary', True)
    if nbr_extended_headers > 3:
        coeffs = extended_headers['2']['number_decimation_filter_coefficient']
        nbr_coeff_remain = coeffs % 8
        for i in range(3, nbr_extended_headers):
            start_byte += 32
            extended_header_name = str(i+1)
            if i == nbr_extended_headers - 1:
                header = _read_extended_header(fi, start_byte, i+1,
                                               nbr_coeff_remain)
                extended_headers[extended_header_name] = header
            else:
                header = _read_extended_header(fi, start_byte, i+1, 8)
                extended_headers[extended_header_name] = header
    return extended_headers


def _read_extended_header_1(fi, start_byte):
    """
    Extract informations contained in the extended header block number 1.
    """
    extended_header_1 = {}
    extended_header_1['id_ru'] = _read(fi, start_byte, 8, 'binary')
    deployment_time = UTCDateTime(_read(fi,
                                        start_byte + 8,
                                        8,
                                        'binary') / 1000000)
    extended_header_1['deployment_time'] = deployment_time
    pick_up_time = UTCDateTime(_read(fi,
                                     start_byte + 16,
                                     8,
                                     'binary') / 1000000)
    extended_header_1['pick_up_time'] = pick_up_time
    start_time_ru = UTCDateTime(_read(fi,
                                      start_byte + 24,
                                      8,
                                      'binary') / 1000000)
    extended_header_1['start_time_ru'] = start_time_ru
    return extended_header_1


def _read_extended_header_2(fi, start_byte):
    """
    Extract informations contained in the extended header block number 2.
    """
    extended_header_2 = {}
    # acquisition drift window in second
    extended_header_2['acquisition_drift_window'] = _read(fi, start_byte, 4,
                                                          'IEEE') * 1e-6
    # clock drift in second
    clock_drift = _read(fi, start_byte + 4, 8, 'binary') * 1e-9
    extended_header_2['clock_drift'] = clock_drift
    leg_clock_stop = {'0': 'normal', '1': 'storage full', '2': 'power loss',
                      '3': 'reboot'}
    key = str(_read(fi, start_byte + 12, 1, 'binary'))
    extended_header_2['clock_stop_method'] = leg_clock_stop[key]
    leg_freq_drift = {'0': 'not within specification',
                      '1': 'within specification'}
    key = str(_read(fi, start_byte + 13, 1, 'binary'))
    extended_header_2['frequency_drift'] = leg_freq_drift[key]
    leg_oscillator_type = {'0': 'control board', '1': 'atomic',
                           '2': 'ovenized', '3': 'double ovenized',
                           '4': 'disciplined'}
    key = str(_read(fi, start_byte + 14, 1, 'binary'))
    extended_header_2['oscillator_type'] = leg_oscillator_type[key]
    leg_data_collection = {'0': 'normal', '1': 'continuous',
                           '2': 'shot sliced with guard band'}
    key = str(_read(fi, start_byte + 15, 1, 'binary'))
    extended_header_2['data_collection_method'] = leg_data_collection[key]
    nbr_time_slices = _read(fi, start_byte + 16, 4, 'binary')
    extended_header_2['nbr_time_slices'] = nbr_time_slices
    extended_header_2['nbr_files'] = _read(fi, start_byte + 20, 4, 'binary')
    extended_header_2['file_number'] = _read(fi, start_byte + 24, 4, 'binary')
    leg_data_decimation = {'0': 'not decimated', '1': 'decimated data'}
    key = str(_read(fi, start_byte + 28, 1, 'binary'))
    extended_header_2['data_decimation'] = leg_data_decimation[key]
    extended_header_2['original_base_scan_interval'] = _read(fi,
                                                             start_byte + 29,
                                                             1, 'binary')
    nbr_dec_coeff = _read(fi, start_byte + 30, 2, 'binary')
    extended_header_2['number_decimation_filter_coefficient'] = nbr_dec_coeff
    return extended_header_2


def _read_extended_header_3(fi, start_byte):
    """
    Extract informations contained in the extended header block number 3.
    """
    extended_header_3 = {}
    extended_header_3['receiver_line_number'] = _read(fi, start_byte, 4,
                                                      'binary')
    extended_header_3['receiver_point'] = _read(fi, start_byte + 4, 4,
                                                'binary')
    extended_header_3['receiver_point_index'] = _read(fi, start_byte + 8,
                                                      1, 'binary')
    extended_header_3['first_shot_line'] = _read(fi, start_byte + 9,
                                                 4, 'binary')
    extended_header_3['first_shot_point'] = _read(fi, start_byte + 13, 4,
                                                  'binary')
    extended_header_3['first_shot_point_index'] = _read(fi, start_byte + 17,
                                                        1, 'binary')
    extended_header_3['last_shot_line'] = _read(fi, start_byte + 18,
                                                4, 'binary')
    extended_header_3['last_shot_point'] = _read(fi, start_byte + 22, 4,
                                                 'binary')
    extended_header_3['last_shot_point_index'] = _read(fi, start_byte + 26,
                                                       1, 'binary')
    return extended_header_3


def _read_extended_header(fi, start_byte, block_number, nbr_coeff):
    """
    Extract informations contained in the ith extended header block (i>3).
    """
    extended_header = {}
    for i in range(0, nbr_coeff):
        key = 'coeff_' + str(i+1)
        extended_header[key] = _read(fi, start_byte, 4, 'IEEE')
        start_byte += 4
    return extended_header
