import itertools
import logging
import multiprocessing
import os
import time
import uuid
import warnings

from numba import jit
import numpy as np
import pandas as pd
import scipy

with warnings.catch_warnings():
    warnings.simplefilter("ignore")

    from rpy2 import robjects
    from rpy2.robjects import pandas2ri
    pandas2ri.activate()
    r_source = robjects.r['source']
    r_assign = robjects.r['assign']
    r_options = robjects.r['options']
    r_options(warn=-1)
    from rpy2.rinterface_lib.callbacks import logger as rpy2_logger
    rpy2_logger.setLevel(logging.ERROR)


from . import setup


ncomp = 7
S, E, I1, I2, I3, R, cumI = np.arange(ncomp)


def onerun_SEIR(s, uid):
    scipy.random.seed()
    r_assign('ti_str', str(s.ti))
    r_assign('tf_str', str(s.tf))
    r_assign('foldername', os.path.join(s.spatset.folder, ""))
    r_source(s.script_npi)
    npi = robjects.r['NPI'].T
    #p.addNPIfromR(npi)

    #r_assign('region', s.spatset.setup_name)
    #r_source(s.script_import)
    #importation = robjects.r['county_importations_total']
    #importation = importation.pivot(index='date', columns='fips_cty', values='importations')
    #importation = importation.fillna(value = 0)
    #importation.index = pd.to_datetime(importation.index)
    #importation.columns = pd.to_numeric(importation.columns)
    #for col in s.spatset.data['geoid']:
    #    if col not in importation.columns:
    #        importation[col] = 0
    #importation = importation.reindex(sorted(importation.columns), axis=1)
    #idx = pd.date_range(s.ti, s.tf)
    #importation = importation.reindex(idx, fill_value=0)
    #importation = importation.to_numpy()
    importation = np.zeros((s.t_span + 3, s.nnodes))

    states = steps_SEIR_nb(setup.parameters_quick_draw(s, npi),
                           s.buildICfromfilter(), uid, s.dt, s.t_inter,
                           s.nnodes, s.popnodes, s.mobility, s.dynfilter,
                           importation)

    # Tidyup data for  R, to save it:
    if s.write_csv:
        a = states.copy()[:, :, ::int(1 / s.dt)]
        a = np.moveaxis(a, 1, 2)
        a = np.moveaxis(a, 0, 1)
        b = np.diff(a, axis=0)
        difI = np.zeros((s.t_span + 1, s.nnodes))
        difI[1:, :] = b[:, cumI, :]
        na = np.zeros((s.t_span + 1, ncomp + 1, s.nnodes))
        na[:, :-1, :] = a
        na[:, -1, :] = difI
        m, n, r = na.shape
        out_arr = np.column_stack((np.tile(np.arange(n),
                                           m), na.reshape(n * m, -1)))
        out_df = pd.DataFrame(
            out_arr,
            columns=['comp'] + list(s.spatset.data['geoid'].astype(int)),
            index=pd.date_range(s.ti, s.tf, freq='D').repeat(ncomp + 1))
        out_df['comp'].replace(S, 'S', inplace=True)
        out_df['comp'].replace(E, 'E', inplace=True)
        out_df['comp'].replace(I1, 'I1', inplace=True)
        out_df['comp'].replace(I2, 'I2', inplace=True)
        out_df['comp'].replace(I3, 'I3', inplace=True)
        out_df['comp'].replace(R, 'R', inplace=True)
        out_df['comp'].replace(cumI, 'cumI', inplace=True)
        out_df['comp'].replace(ncomp, 'diffI', inplace=True)
        str(uuid.uuid4())[:2]
        out_df.to_csv(
            f"{s.datadir}{s.timestamp}_{s.setup_name}_{str(uuid.uuid4())}.csv",
            index='time',
            index_label='time')

    return 1


def run_parallel(s, processes):

    tic = time.time()
    uids = np.arange(s.nsim)

    with multiprocessing.Pool(processes=processes) as pool:
        result = pool.starmap(
            onerun_SEIR,
            zip(
                itertools.repeat(s),
                #itertools.repeat(p),
                uids))
    print(f">>> {s.nsim}  Simulations done in {time.time()-tic} seconds...")
    return result


#@jit(float64[:,:,:](float64[:,:], float64[:], int64), nopython=True)
@jit(nopython=True)
def steps_SEIR_nb(p_vec, y0, uid, dt, t_inter, nnodes, popnodes, mobility,
                  dynfilter, importation):
    """
        Made to run just-in-time-compiled by numba, hence very descriptive and using loop,
        because loops are expanded by the compiler hence not a problem.
        as there is very few authorized function. Needs the nopython option to be fast.
    """
    #np.random.seed(uid)
    t = 0

    y = np.copy(y0)
    states = np.zeros((ncomp, nnodes, len(t_inter)))

    mv = np.empty(ncomp - 1)
    exposeCases = np.empty(nnodes)
    incidentCases = np.empty(nnodes)
    incident2Cases = np.empty(nnodes)
    incident3Cases = np.empty(nnodes)
    recoveredCases = np.empty(nnodes)

    p_infect = 1 - np.exp(-dt * p_vec[1][0][0])
    p_recover = 1 - np.exp(-dt * p_vec[2][0][0])

    for it, t in enumerate(t_inter):
        if (it % int(1 / dt) == 0):
            y[E] = y[E] + importation[int(t)]
        for ori in range(nnodes):
            for dest in range(nnodes):
                for c in range(ncomp - 1):
                    mv[c] = np.random.binomial(
                        y[c, ori],
                        1 - np.exp(-dt * mobility[ori, dest] / popnodes[ori]))
                y[:-1, dest] += mv
                y[:-1, ori] -= mv

        p_expose = 1 - np.exp(-dt * p_vec[0][it] *
                              (y[I1] + y[I2] + y[I3]) / popnodes)  # vector

        for i in range(nnodes):
            exposeCases[i] = np.random.binomial(y[S][i], p_expose[i])
            incidentCases[i] = np.random.binomial(y[E][i], p_infect)
            incident2Cases[i] = np.random.binomial(y[I1][i], p_recover)
            incident3Cases[i] = np.random.binomial(y[I2][i], p_recover)
            recoveredCases[i] = np.random.binomial(y[I3][i], p_recover)

        y[S] += -exposeCases
        y[E] += exposeCases - incidentCases
        y[I1] += incidentCases - incident2Cases
        y[I2] += incident2Cases - incident3Cases
        y[I3] += incident3Cases - recoveredCases
        y[R] += recoveredCases
        y[cumI] += incidentCases
        states[:, :, it] = y
        if (it % int(1 / dt) == 0):
            y[cumI] += importation[int(t)]
        #if (it%(1/dt) == 0 and (y[cumI] <= dynfilter[int(it%(1/dt))]).any()):
        #        return -np.ones((ncomp, nnodes, len(t_inter)))

    return states
