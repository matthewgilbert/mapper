import pandas as pd
import numpy as np
import cvxpy


def roller(timestamps, contract_dates, get_weights, **kwargs):
    """
    Calculate weight allocations to tradeable instruments for generic futures
    at a set of timestamps.

    Paramters
    ---------
    timestamps: iterable
        Sorted iterable of of pandas.Timestamps to calculate weights for
    contract_dates: pandas.Series
        Series with index of tradeable contract names and pandas.Timestamps
        representing the last date of the roll as values
    get_weights: function
        A function which takes in a timestamp, contract_dates and **kwargs and
        returns a list of tuples consisting of the generic instrument name,
        the tradeable contract as a string, the weight on this contract
        as a float and the date as a pandas.Timestamp.
    kwargs: keyword arguments
        Arguements to pass to get_weights

    Return
    ------
    A pandas.DataFrame with columns representing generics and a MultiIndex of
    date and contract. Values represent weights on tradeables for each generic.

    Examples
    --------
    >>> cols = pd.MultiIndex.from_product([["CL1", "CL2"], ['front', 'back']])
    >>> idx = [-2, -1, 0]
    >>> trans = pd.DataFrame([[1.0, 0.0, 1.0, 0.0], [0.5, 0.5, 0.5, 0.5],
    ...                       [0.0, 1.0, 0.0, 1.0]], index=idx, columns=cols)
    >>> contract_dates = pd.Series([pd.Timestamp('2016-10-20'),
    ...                             pd.Timestamp('2016-11-21'),
    ...                             pd.Timestamp('2016-12-20')],
    ...                            index=['CLX16', 'CLZ16', 'CLF17'])
    >>> ts = pd.DatetimeIndex([pd.Timestamp('2016-10-18'),
    ...                        pd.Timestamp('2016-10-19'),
    ...                        pd.Timestamp('2016-10-19')])
    >>> wts = mappings.roller(ts, contract_dates, mappings.static_transition,
    ...                       transition=trans)
    """
    timestamps = sorted(timestamps)
    contract_dates = contract_dates.sort_values()
    weights = []
    for ts in timestamps:
        weights.extend(get_weights(ts, contract_dates, **kwargs))

    weights = aggregate_weights(weights)

    return weights


def aggregate_weights(weights, drop_date=False):
    """
    Transforms list of tuples of weights into pandas.DataFrame of weights.

    Parameters:
    -----------
    weights: list
        A list of tuples consisting of the generic instrument name,
        the tradeable contract as a string, the weight on this contract as a
        float and the date as a pandas.Timestamp.
    drop_date: boolean
        Whether to drop the date from the multiIndex

    Returns
    -------
    A pandas.DataFrame of loadings of generic contracts on tradeable
    instruments for a given date. The columns are generic instrument names and
    the index is strings representing instrument names.
    """
    dwts = pd.DataFrame(weights,
                        columns=["generic", "contract", "weight", "date"])
    dwts = dwts.pivot_table(index=['date', 'contract'],
                            columns=['generic'], values='weight', fill_value=0)
    dwts = dwts.astype(float)
    dwts = dwts.sort_index()
    if drop_date:
        dwts.index = dwts.index.levels[-1]
    return dwts


def static_transition(timestamp, contract_dates, transition, holidays=None):
    """
    An implementation of *get_weights* parameter in roller().
    Return weights to tradeable instruments for a given date based on a
    transition DataFrame which indicates how to roll through the roll period.

    Parameters
    ----------
    timestamp: pandas.Timestamp
        The timestamp to return instrument weights for
    contract_dates: pandas.Series
        Series with index of tradeable contract names and pandas.Timestamps
        representing the last date of the roll as values, sorted by values.
    transition: pandas.DataFrame
        A DataFrame with a index of integers representing business day offsets
        from the last roll date and a column which is a MultiIndex where the
        top level is generic instruments and the second level is
        ['front', 'back'] which refer to the front month contract and the back
        month contract of the roll. Note that for different generics, e.g. CL1,
        CL2, the front and back month contract during a roll would refer to
        different underlying instruments. The values represent the fraction of
        the roll on each day during the roll period. The first row of the
        transition period should be completely allocated to the front contract
        and the last row should be completely allocated to the back contract.
    holidays: array_like of datetime64[D]
        Holidays to exclude when calculating business day offsets from the last
        roll date. See numpy.busday_count.

    Returns
    -------
    A list of tuples consisting of the generic instrument name, the tradeable
    contract as a string, the weight on this contract as a float and the date
    as a pandas.Timestamp.

    Examples
    --------
    >>> cols = pd.MultiIndex.from_product([["CL1", "CL2"], ['front', 'back']])
    >>> idx = [-2, -1, 0]
    >>> transition = pd.DataFrame([[1.0, 0.0, 1.0, 0.0], [0.5, 0.5, 0.5, 0.5],
    ...                            [0.0, 1.0, 0.0, 1.0]],
    ...                           index=idx, columns=cols)
    >>> contract_dates = pd.Series([pd.Timestamp('2016-10-20'),
    ...                             pd.Timestamp('2016-11-21'),
    ...                             pd.Timestamp('2016-12-20')],
    ...                            index=['CLX16', 'CLZ16', 'CLF17'])
    >>> ts = pd.Timestamp('2016-10-19')
    >>> wts = mappings.static_transition(ts, contract_dates, transition)
    """

    if not holidays:
        holidays = []

    # further speedup can be obtained using contract_dates.loc[timestamp:]
    # but this requires swapping contract_dates index and values
    after_contract_dates = contract_dates.loc[contract_dates >= timestamp]
    contracts = after_contract_dates.index
    front_expiry_dt = after_contract_dates.iloc[0]
    days_to_expiry = np.busday_count(front_expiry_dt, timestamp,
                                     holidays=holidays)

    name2num = dict(zip(transition.columns.levels[0],
                        range(len(transition.columns.levels[0]))))
    if days_to_expiry in transition.index:
        weights_iter = transition.loc[days_to_expiry].iteritems()
    # roll hasn't started yet
    elif days_to_expiry < transition.index.min():
        # provides significant speedup over transition.iloc[0].iteritems()
        vals = transition.values[0]
        weights_iter = zip(transition.columns.tolist(), vals)
    # roll is finished
    else:
        vals = transition.values[-1]
        weights_iter = zip(transition.columns.tolist(), vals)

    cwts = []
    for idx_tuple, weighting in weights_iter:
        gen_name, position = idx_tuple
        if weighting != 0:
            if position == "front":
                cntrct_idx = name2num[gen_name]
            elif position == "back":
                cntrct_idx = name2num[gen_name] + 1
            else:
                raise ValueError("transition.columns must contain "
                                 "'front' or 'back'")
            try:
                cwts.append((gen_name, contracts[cntrct_idx], weighting, timestamp))  # NOQA
            except IndexError as e:
                import sys
                raise type(e)(str(e) + ". No 'back' contract for %s\nInsufficient 'contract_dates', last row:\n%s" % (timestamp, contract_dates.iloc[[-1]])).with_traceback(sys.exc_info()[2])  # NOQA

    return cwts


def to_generics(instruments, weights):
    """
    Map tradeable instruments to generics given weights and tradeable
    instrument holdings. This is solving the equation Ax = b where A is the
    weights, and b is the instrument holdings. When Ax = b has no solution we
    solve for x' such that Ax' is closest to b in the least squares sense with
    the additional constraint that sum(x') = sum(instruments).

    Scenarios with exact solutions and non exact solutions are depicted below

    +------------+-----+-----+ Instruments
    | contract   | CL1 | CL2 | ------------------------------------
    |------------+-----+-----| Scenario 1 | Scenario 2 | Scenario 3
    | CLX16      | 0.5 | 0   | 10         | 10         | 10
    | CLZ16      | 0.5 | 0.5 | 20         | 20         | 25
    | CLF17      | 0   | 0.5 | 10         | 11         | 11
    +------------+-----+-----+

    In scenario 1 the solution is given by x = [20, 20], in scenario 2 the
    solution is given by x = [19.5, 21.5], and in scenario 3 the solution is
    given by x = [22, 24].

    NOTE: Integer solutions are not guruanteed, as demonstrated above. This is
    intended for use with contract numbers but can also be used with notional
    amounts of contracts.

    Parameters
    ----------
    instruments: pandas.Series
        Series of tradeable instrument holdings where the index is the name of
        the tradeable instrument and the value is the number of that instrument
        held.
    weights: pandas.DataFrame or dict
        A pandas.DataFrame of loadings of generic contracts on tradeable
        instruments for a given date. The columns are generic instruments
        and the index is strings representing instrument names. If dict is
        given keys should be root generic, e.g. 'CL', and values should be
        pandas.DataFrames of loadings. The union of all indexes should be a
        superset of the instruments.index

    Returns
    -------
    A pandas.Series where the index is the generic and the value is the number
    of contracts, sorted by index.

    Examples
    --------
    >>> wts = pd.DataFrame([[0.5, 0], [0.5, 0.5], [0, 0.5]],
    ...                    index=["CLX16", "CLZ16", "CLF17"],
    ...                    columns=["CL1", "CL2"])
    >>> instrs = pd.Series([10, 20, 10], index=["CLX16", "CLZ16", "CLF17"])
    >>> generics = mappings.to_generics(instrs, wts)
    """
    if not isinstance(weights, dict):
        weights = {"": weights}

    allocations = []
    unmapped_instr = instruments.index
    for key in weights:
        w = weights[key]
        # may not always have instrument holdings for a set of weights so allow
        # weights to be a superset of instruments, drop values where no
        # holdings
        winstrs = instruments.loc[w.index].dropna()
        w = w.loc[winstrs.index]

        unmapped_instr = unmapped_instr.difference(winstrs.index)

        A = w.values
        b = winstrs.values
        x = cvxpy.Variable(A.shape[1])
        constrs = [cvxpy.sum_entries(x) == np.sum(b)]
        obj = cvxpy.Minimize(cvxpy.sum_squares(A * x - b))
        prob = cvxpy.Problem(obj, constrs)
        prob.solve()

        vals = np.array(x.value).squeeze()
        idx = w.columns.tolist()
        allocations.append(pd.Series(vals, index=idx))

    if len(unmapped_instr) > 0:
        raise KeyError("Unmapped instruments %s. weights must be a superset of"
                       " instruments" % unmapped_instr.tolist())

    allocations = pd.concat(allocations, axis=0)
    allocations = allocations.sort_index()
    return allocations