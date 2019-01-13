import numpy as np
from astropy.modeling.blackbody import blackbody_nu
from ... import units
from pathlib import Path
from ..template import Model, check_freq_input, read_map
import healpy as hp

class ModifiedBlackBody(Model):
    """ This is a model for modified black body emission.

    Attributes
    ----------
    I_ref, Q_ref, U_ref: ndarray
        Arrays containing the intensity or polarization reference
        templates at frequency `freq_ref_I` or `freq_ref_P`.
    """
    def __init__(self, map_I=None, map_Q=None, map_U=None, unit_I=None,
                 unit_Q=None, unit_U=None, freq_ref_I=None,
                 freq_ref_P=None, map_mbb_index=None, map_mbb_temperature=None,
                 nside=None, mpi_comm=None):
        """ This function initializes the modified black body model.

        The initialization of this model consists of reading in emission
        templates from file, reading in spectral parameter maps from
        file.

        Parameters
        ----------
        map_I, map_Q, map_U: `pathlib.Path` object
            Paths to the maps to be used as I, Q, U templates.
        freq_ref_I, freq_ref_P: float
            Reference frequencies at which the intensity and polarization
            templates are defined.
        map_mbb_index: `pathlib.Path` object
            Path to the map to be used as the power law index for the dust
            opacity in a modified blackbody model.
        map_mbb_temperature: `pathlib.Path` object
            Path to the map to be used as the temperature of the dust in a
            modified blackbody model.
        nside: int
            Resolution parameter at which this model is to be calculated.
        """
        Model.__init__(self, mpi_comm)
        # do model setup
        self.__mbb_index = read_map(map_mbb_index, nside)[None, :]
        self.__mbb_temperature = read_map(map_mbb_temperature, nside)[None, :] * units.K

        freq_ref_I = float(freq_ref_I) * units.GHz
        freq_ref_P = float(freq_ref_P) * units.GHz
        self.__iqu_ref_freqs = units.Quantity([freq_ref_I] + 2* [freq_ref_P])

        npix = hp.nside2npix(nside)
        self.__iqu_ref = np.empty((3, npix)) * units.uK_RJ
        self.__iqu_ref[0] = read_map(map_I, nside)[None, :] * units.uK_RJ
        self.__iqu_ref[1] = read_map(map_Q, nside)[None, :] * units.uK_RJ
        self.__iqu_ref[2] = read_map(map_U, nside)[None, :] * units.uK_RJ

        @property
        def freq_ref_I(self):
            return self.__freq_ref_I
    
        @freq_ref_I.setter
        def freq_ref_I(self, value):
            if value < 0:
                raise InputParameterError
            try:
                assert(isinstance(value, units.Quantity))
            except AssertionError:
                raise InputParameterError(
                    r"""Must be instance of `astropy.units.Quantity`, with 
                    Hz equivalency.""")
            self.__freq_ref_I = value

        @property
        def freq_ref_P(self):
            return self.__freq_ref_P
    
        @freq_ref_P.setter
        def freq_ref_P(self, value):
            if value < 0:
                raise InputParameterError
            try:
                assert(isinstance(value, units.Quantity))
            except AssertionError:
                raise InputParameterError(
                    r"""Must be instance of `astropy.units.Quantity`, with 
                    Hz equivalency.""")
            self.__freq_ref_P = value
        

    @units.quantity_input
    def get_emission(self, freqs: units.GHz) -> units.uK_RJ:
        """ This function evaluates the component model at a either
        a single frequency, an array of frequencies, or over a bandpass.

        Parameters
        ----------
        freqs: float
            Frequency at which the model should be evaluated, assumed to be
            given in GHz.

        Returns
        -------
        ndarray
            Set of maps at the given frequency or frequencies. This will have
            shape (nfreq, 3, npix).
        """
        freqs = check_freq_input(freqs)
        # calculate scaling, shape will be (nfreqs, npol, npix)
        scaling = mbb_sed(freqs[:, None, None], self.__iqu_ref_freqs[None, :, None],
                          self.__mbb_index[None, None, :],
                          self.__mbb_temperature[None, None, :])
        # multiply scaling by templates, shape will be (nfreqs, npol, npix)
        return scaling * self.__iqu_ref[None, ...]

class DecorrelatedModifiedBlackBody(ModifiedBlackBody):
    def __init__(self, map_I=None, map_Q=None, map_U=None, freq_ref_I=None,
                 freq_ref_P=None, map_mbb_index=None, map_mbb_temperature=None,
                 nside=None, mpi_comm=None, correlation_length=None):
        """ See parent class for other documentation.
        
        Parameters
        ----------
        correlation_length: float
            This number set the scale in logarithmic space for the distance in
            freuqency past which the MBB emission becomes decorrelated. For
            frequencies much much closer than this distance, the emission is
            well correlated.
        """
        ModifiedBlackBody.__init__(self, map_I, map_Q, map_U, freq_ref_I,
                                   freq_ref_P, map_mbb_index,
                                   map_mbb_temperature, nside, mpi_comm)
        self.correlation_length = correlation_length

    def get_emission(self, freqs):
        """ Function to calculate the emission of a decorrelated modified black
        body model.
        """
        freqs = check_freq_input(freqs)
        # calculate the decorrelation
        (rho_cov_I, rho_mean_I) = get_decorrelation_matrix(self.freq_ref_I,
                                                           freqs,
                                                           self.correlation_length)
        (rho_cov_P, rho_mean_P) = get_decorrelation_matrix(self.freq_ref_P,
                                                           freqs,
                                                           self.correlation_length)
        nfreqs = freqs.shape[-1]
        extra_I = np.dot(rho_cov_I, np.random.randn(nfreqs))
        extra_P = np.dot(rho_cov_P, np.random.randn(nfreqs))
        decorr = np.zeros((nfreqs, 3))
        decorr[:, 0, None] = rho_mean_I + extra_I[:, None]
        decorr[:, 1, None] = rho_mean_P + extra_P[:, None]
        decorr[:, 2, None] = rho_mean_P + extra_P[:, None]
        # apply the decorrelation to the mbb_emission
        return decorr[..., None] * super().get_emission(freqs)

@units.quantity_input(freqs=units.GHz,
                      correlation_length=units.dimensionless_unscaled)
def frequency_decorr_model(freqs, correlation_length) -> units.dimensionless_unscaled:
    """ Function to calculate the frequency decorrelation method of
    Vansyngel+17.
    """
    log_dep = np.log(freqs[:, None] / freqs[None, :])
    return np.exp(- 0.5 * (log_dep / correlation_length) ** 2)

@units.quantity_input(freq_constrained=units.GHz, freqs_constrained=units.GHz,
                      correlation_length=units.dimensionless_unscaled)
def get_decorrelation_matrix(freq_constrained, freqs_unconstrained,
                             correlation_length) -> units.dimensionless_unscaled:
    """ Function to calculate the correlation matrix between observed
    frequencies. This model is based on the proposed model for decorrelation
    of Vanyngel+17. The proposed frequency covariance matrix in this paper
    is implemented, and a constrained Gaussian realization for the unobserved
    frequencies is calculated.

    Notes
    -----
    For a derivation see 1109.0286.

    Parameters
    ----------
    freq_constrained: float
        Reference frequency.
    freqs_unconstrained: ndarray
        Frequencies at which to calculate the correlation matrix.
    correlation_length: float
         Parameter controlling the structure of the frequency covariance matrix.

    Returns
    -------
    ndarray
        Frequency covariance matrix used to calculate a constrained realization.
    """
    assert(correlation_length >= 0)
    assert(isinstance(freqs_unconstrained, np.ndarray))
    freq_constrained = check_freq_input(freq_constrained)
    freqs_all = np.insert(freqs_unconstrained, 0, freq_constrained)
    indref = np.where(freqs_all == freq_constrained)
    corrmatrix = frequency_decorr_model(freqs_all, correlation_length)
    rho_inv = invert_safe(corrmatrix)
    rho_uu = np.delete(np.delete(rho_inv, indref, axis=0), indref, axis=1)
    rho_uu = invert_safe(rho_uu)
    rho_inv_cu = rho_inv[:, indref]
    rho_inv_cu = np.transpose(np.array([np.delete(rho_inv_cu, indref)]))
    # get eigenvalues, w, and eigenvectors in matrix, v.
    rho_uu_w, rho_uu_v = np.linalg.eigh(rho_uu)
    # reconstruct covariance matrix using only positive eigenvalues. Take
    # square root as we use this to draw directly the pixels (sigma).
    evals = np.diag(np.sqrt(np.maximum(rho_uu_w, np.zeros_like(rho_uu_w))))
    rho_covar = np.dot(rho_uu_v, np.dot(evals, np.transpose(rho_uu_v)))
    rho_mean = - np.dot(rho_uu, rho_inv_cu)
    return (rho_covar, rho_mean)


def invert_safe(matrix):
    """Function to safely invert almost positive definite matrix.

    Parameters
    ----------
    matrix: ndarray
        matrix to invert.

    Returns
    -------
    ndaray
        inverted matrix.
    """
    mb = matrix.copy()
    w_ok = False
    while not w_ok:
        w, v = np.linalg.eigh(mb)
        wmin = np.min(w)
        if wmin > 0:
            w_ok = True
        else:
            mb += np.diag(2. * np.max([1E-14, -wmin]) * np.ones(len(mb)))
    winv = 1. / w
    return np.dot(v, np.dot(np.diag(winv), np.transpose(v)))

@units.quantity_input(freq_to=units.GHz, freq_from=units.GHz, temp=units.K)
def blackbody_ratio(freq_to, freq_from, temp) -> units.dimensionless_unscaled:
    """ Function to calculate the flux ratio between two frequencies for a
    blackbody at a given temperature.

    Parameters
    ----------
    freq_to: float
        Frequency to which to scale assuming black body SED.
    freq_from: float
        Frequency from which to scale assuming black body SED.
    temp: float
        Temperature of the black body.

    Returns
    -------
    float
        Black body ratio between `freq_to` and `freq_from` at temperature
        `temp`.
    """
    return blackbody_nu(freq_to, temp) / blackbody_nu(freq_from, temp)

@units.quantity_input(freqs_to=units.GHz, freq_from=units.GHz, index=units.dimensionless_unscaled, temp=units.K)
def mbb_sed(freqs_to, freq_from, index, temp) -> units.dimensionless_unscaled:
    """ Function to calculate the scaling factor between frequencies freqs_to, and freq_from
    assuming a blackbody sed.

    Note that this assumes that the emission template being multiplied is in units of
    uK_RJ, since we use beta - 2 as the index of the power law.

    Parameters
    ----------
    freqs_to: astropy.units.Quantity
        Array of frequencies to which we calculate the MBB scaling factor.
    freq_from: astropy.units.Quantity
    temp: astropy.units.Quantity
    beta: astropy.units.Quantity

    Returns
    -------
    astropy.units.Quantity
        Array of scaling factors.
    """
    return (freqs_to / freq_from) ** (index - 2) * blackbody_ratio(freqs_to, freq_from, temp)
