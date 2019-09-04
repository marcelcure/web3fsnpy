from abc import (
    ABC,
    abstractmethod,
)
import json
from pathlib import (
    Path,
)
from typing import (
    Any,
    Dict,
    Iterable,
    NewType,
    Tuple,
)

from eth_typing import (
    URI,
    Address,
    Manifest,
)
from eth_utils import (
    is_canonical_address,
    is_checksum_address,
    to_checksum_address,
    to_text,
    to_tuple,
)

from ethpm import (
    ASSETS_DIR,
    Package,
)
from ethpm.uri import (
    is_supported_content_addressed_uri,
    resolve_uri_contents,
)
from ethpm.validation.manifest import (
    validate_manifest_against_schema,
    validate_raw_manifest_format,
)
from ethpm.validation.package import (
    validate_package_name,
    validate_package_version,
)
from web3fsnpy import .Web3Fsn
from web3fsnpy._utils.ens import (
    is_ens_name,
)
from web3fsnpy.exceptions import (
    InvalidAddress,
    ManifestValidationError,
    NameNotFound,
    PMError,
)
from web3fsnpy.module import (
    Module,
)

TxReceipt = NewType("TxReceipt", Dict[str, Any])


# Package Management is still in alpha, and its API is likely to change, so it
# is not automatically available on a web3fsnpy instance. To use the `PM` module,
# please enable the package management API on an individual web3fsnpy instance.
#
# >>> from web3fsnpy.auto import w3
# >>> w3.pm
# AttributeError: The Package Management feature is disabled by default ...
# >>> w3.enable_unstable_package_management_api()
# >>> w3.pm
# <web3fsnpy.pm.PM at 0x....>


class ERC1319Registry(ABC):
    """
    The ERC1319Registry class is a base class for all registry implementations to inherit from. It
    defines the methods specified in `ERC 1319 <https://github.com/ethereum/EIPs/issues/1319>`__.
    All of these methods are prefixed with an underscore, since they are not intended to be
    accessed directly, but rather through the methods on ``web3fsnpy.pm``. They are unlikely to change,
    but must be implemented in a `ERC1319Registry` subclass in order to be compatible with the
    `PM` module. Any custom methods (eg. not definied in ERC1319) in a subclass
    should *not* be prefixed with an underscore.

    All of these methods must be implemented in any subclass in order to work with `web3fsnpy.pm.PM`.
    Any implementation specific logic should be handled in a subclass.
    """

    @abstractmethod
    def __init__(self, address: Address, w3: .Web3Fsn) -> None:
        """
        Initializes the class with the on-chain address of the registry, and a web3fsnpy instance
        connected to the chain where the registry can be found.

        Must set the following properties...

        * ``self.registry``: A `web3fsnpy.contract` instance of the target registry.
        * ``self.address``: The address of the target registry.
        * ``self.w3``: The *web3fsnpy* instance connected to the chain where the registry can be found.
        """
        pass

    #
    # Write API
    #

    @abstractmethod
    def _release(self, package_name: str, version: str, manifest_uri: str) -> bytes:
        """
        Returns the releaseId created by successfully adding a release to the registry.

        * Parameters:
            * ``package_name``: Valid package name according the spec.
            * ``version``: Version identifier string, can conform to any versioning scheme.
            * ``manifest_uri``: URI location of a manifest which details the release contents
        """
        pass

    #
    # Read API
    #

    @abstractmethod
    def _get_package_name(self, package_id: bytes) -> str:
        """
        Returns the package name associated with the given package id, if the
        package id exists on the connected registry.

        * Parameters:
            * ``package_id``: 32 byte package identifier.
        """
        pass

    @abstractmethod
    def _get_all_package_ids(self) -> Tuple[bytes]:
        """
        Returns a tuple containing all of the package ids found on the connected registry.
        """
        pass

    @abstractmethod
    def _get_release_id(self, package_name: str, version: str) -> bytes:
        """
        Returns the 32 bytes release id associated with the given package name and version,
        if the release exists on the connected registry.

        * Parameters:
            * ``package_name``: Valid package name according the spec.
            * ``version``: Version identifier string, can conform to any versioning scheme.
        """
        pass

    @abstractmethod
    def _get_all_release_ids(self, package_name: str) -> Tuple[bytes]:
        """
        Returns a tuple containg all of the release ids belonging to the given package name,
        if the package has releases on the connected registry.

        * Parameters:
            * ``package_name``: Valid package name according the spec.
        """
        pass

    @abstractmethod
    def _get_release_data(self, release_id: bytes) -> Tuple[str, str, str]:
        """
        Returns a tuple containing (package_name, version, manifest_uri) for the given release id,
        if the release exists on the connected registry.

        * Parameters:
            * ``release_id``: 32 byte release identifier.
        """
        pass

    @abstractmethod
    def _generate_release_id(self, package_name: str, version: str) -> bytes:
        """
        Returns the 32 byte release identifier that *would* be associated with the given
        package name and version according to the registry's hashing mechanism.
        The release *does not* have to exist on the connected registry.

        * Parameters:
            * ``package_name``: Valid package name according the spec.
            * ``version``: Version identifier string, can conform to any versioning scheme.
        """
        pass

    @abstractmethod
    def _num_package_ids(self) -> int:
        """
        Returns the number of packages that exist on the connected registry.
        """
        pass

    @abstractmethod
    def _num_release_ids(self, package_name: str) -> int:
        """
        Returns the number of releases found on the connected registry,
        that belong to the given package name.

        * Parameters:
            * ``package_name``: Valid package name according the spec.
        """
        pass

    @abstractmethod
    def deploy_new_instance(cls, w3):
        """
        Class method that returns a newly deployed instance of ERC1319Registry.

        * Parameters:
            * ``w3``: .Web3Fsn instance on which to deploy the new registry.
        """
        pass


BATCH_SIZE = 100


class SimpleRegistry(ERC1319Registry):
    """
    This class represents an instance of the `Solidity Reference Registry implementation
    <https://github.com/ethpm/solidity-registry>`__.
    """

    def __init__(self, address: Address, w3: .Web3Fsn) -> None:
        abi = get_simple_registry_manifest()["contract_types"]["PackageRegistry"][
            "abi"
        ]
        self.registry = w3.fsn.contract(address=address, abi=abi)
        self.address = to_checksum_address(address)
        self.w3 = w3

    def _release(self, package_name: str, version: str, manifest_uri: str) -> bytes:
        tx_hash = self.registry.functions.release(
            package_name, version, manifest_uri
        ).transact()
        self.w3.fsn.waitForTransactionReceipt(tx_hash)
        return self._get_release_id(package_name, version)

    def _get_package_name(self, package_id: bytes) -> str:
        package_name = self.registry.functions.getPackageName(package_id).call()
        return package_name

    @to_tuple
    def _get_all_package_ids(self) -> Iterable[Tuple[bytes]]:
        num_packages = self._num_package_ids()
        pointer = 0
        while pointer < num_packages:
            new_ids, new_pointer = self.registry.functions.getAllPackageIds(
                pointer,
                (pointer + BATCH_SIZE)
            ).call()
            if not new_pointer > pointer:
                break
            yield from reversed(new_ids)
            pointer = new_pointer

    def _get_release_id(self, package_name: str, version: str) -> bytes:
        return self.registry.functions.getReleaseId(package_name, version).call()

    @to_tuple
    def _get_all_release_ids(self, package_name: str) -> Iterable[Tuple[bytes]]:
        num_releases = self._num_release_ids(package_name)
        pointer = 0
        while pointer < num_releases:
            new_ids, new_pointer = self.registry.functions.getAllReleaseIds(
                package_name,
                pointer,
                (pointer + BATCH_SIZE)
            ).call()
            if not new_pointer > pointer:
                break
            yield from reversed(new_ids)
            pointer = new_pointer

    @to_tuple
    def _get_release_data(self, release_id: bytes) -> Iterable[Tuple[str]]:
        release_data = self.registry.functions.getReleaseData(release_id).call()
        for data in release_data:
            yield data

    def _generate_release_id(self, package_name: str, version: str) -> bytes:
        return self.registry.functions.generateReleaseId(package_name, version).call()

    def _num_package_ids(self) -> int:
        return self.registry.functions.numPackageIds().call()

    def _num_release_ids(self, package_name: str) -> int:
        return self.registry.functions.numReleaseIds(package_name).call()

    @classmethod
    def deploy_new_instance(cls, w3):
        manifest = get_simple_registry_manifest()
        registry_package = Package(manifest, w3)
        registry_factory = registry_package.get_contract_factory("PackageRegistry")
        tx_hash = registry_factory.constructor().transact()
        tx_receipt = w3.fsn.waitForTransactionReceipt(tx_hash)
        return cls(tx_receipt.contractAddress, w3)


class PM(Module):
    """
    The PM module will work with any subclass of ``ERC1319Registry``, tailored to a particular
    implementation of `ERC1319  <https://github.com/ethereum/EIPs/issues/1319>`__, set as
    its ``registry`` attribute.
    """

    def get_package_from_manifest(self, manifest: Manifest) -> Package:
        """
        Returns a `Package <https://github.com/ethpm/py-ethpm/blob/master/ethpm/package.py>`__
        instance built with the given manifest.

        * Parameters:
            * ``manifest``: A dict representing a valid manifest
        """
        return Package(manifest, self.web3fsnpy)

    def get_package_from_uri(self, manifest_uri: URI) -> Package:
        """
        Returns a `Package <https://github.com/ethpm/py-ethpm/blob/master/ethpm/package.py>`__
        instance built with the Manifest stored at the URI.
        If you want to use a specific IPFS backend, set ``ETHPM_IPFS_BACKEND_CLASS``
        to your desired backend. Defaults to Infura IPFS backend.

        * Parameters:
            * ``uri``: Must be a valid content-addressed URI
        """
        return Package.from_uri(manifest_uri, self.web3fsnpy)

    def get_local_package(self, package_name: str, ethpm_dir: Path = None) -> Package:
        """
        Returns a `Package <https://github.com/ethpm/py-ethpm/blob/master/ethpm/package.py>`__
        instance built with the Manifest found at the package name in your local ethpm_dir.

        * Parameters:
            * ``package_name``: Must be the name of a package installed locally.
            * ``ethpm_dir``: Path pointing to the target ethpm directory (optional).
        """
        if not ethpm_dir:
            ethpm_dir = Path.cwd() / '_ethpm_packages'

        if not ethpm_dir.name == "_ethpm_packages" or not ethpm_dir.is_dir():
            raise PMError(f"{ethpm_dir} is not a valid ethPM packages directory.")

        local_packages = [pkg.name for pkg in ethpm_dir.iterdir() if pkg.is_dir()]
        if package_name not in local_packages:
            raise PMError(
                f"Package: {package_name} not found in {ethpm_dir}. "
                f"Available packages include: {local_packages}."
            )

        target_manifest = json.loads(
            (ethpm_dir / package_name / "manifest.json").read_text()
        )
        return self.get_package_from_manifest(target_manifest)

    def set_registry(self, address: Address) -> None:
        """
        Sets the current registry used in ``web3fsnpy.pm`` functions that read/write to an on-chain
        registry. This method accepts checksummed/canonical addresses or ENS names. Addresses
        must point to an on-chain instance of an ERC1319 registry implementation.

        To use an ENS domain as the address, make sure a valid ENS instance set as ``web3fsnpy.ens``.

        * Parameters:
            * ``address``: Address of on-chain Registry.
        """
        if is_canonical_address(address) or is_checksum_address(address):
            self.registry = SimpleRegistry(address, self.web3fsnpy)
        elif is_ens_name(address):
            self._validate_set_ens()
            addr_lookup = self.web3fsnpy.ens.address(address)
            if not addr_lookup:
                raise NameNotFound(
                    "No address found after ENS lookup for name: {0}.".format(address)
                )
            self.registry = SimpleRegistry(addr_lookup, self.web3fsnpy)
        else:
            raise PMError(
                "Expected a canonical/checksummed address or ENS name for the address, "
                "instead received {0}.".format(type(address))
            )

    def deploy_and_set_registry(self) -> Address:
        """
        Returns the address of a freshly deployed instance of `SimpleRegistry`
        and sets the newly deployed registry as the active registry on ``web3fsnpy.pm.registry``.

        To tie your registry to an ENS name, use web3fsnpy's ENS module, ie.

        .. code-block:: python

           w3.ens.setup_address(ens_name, w3.pm.registry.address)
        """
        self.registry = SimpleRegistry.deploy_new_instance(self.web3fsnpy)
        return to_checksum_address(self.registry.address)

    def release_package(
        self, package_name: str, version: str, manifest_uri: str
    ) -> bytes:
        """
        Returns the release id generated by releasing a package on the current registry.
        Requires ``web3fsnpy.PM`` to have a registry set. Requires ``web3fsnpy.fsn.defaultAccount``
        to be the registry owner.

        * Parameters:
            * ``package_name``: Must be a valid package name, matching the given manifest.
            * ``version``: Must be a valid package version, matching the given manifest.
            * ``manifest_uri``: Must be a valid content-addressed URI. Currently, only IPFS
              and Github content-addressed URIs are supported.

        """
        validate_is_supported_manifest_uri(manifest_uri)
        raw_manifest = to_text(resolve_uri_contents(manifest_uri))
        validate_raw_manifest_format(raw_manifest)
        manifest = json.loads(raw_manifest)
        validate_manifest_against_schema(manifest)
        if package_name != manifest["package_name"]:
            raise ManifestValidationError(
                f"Provided package name: {package_name} does not match the package name "
                f"found in the manifest: {manifest['package_name']}."
            )

        if version != manifest["version"]:
            raise ManifestValidationError(
                f"Provided package version: {version} does not match the package version "
                f"found in the manifest: {manifest['version']}."
            )

        self._validate_set_registry()
        return self.registry._release(package_name, version, manifest_uri)

    @to_tuple
    def get_all_package_names(self) -> Iterable[str]:
        """
        Returns a tuple containing all the package names available on the current registry.
        """
        self._validate_set_registry()
        package_ids = self.registry._get_all_package_ids()
        for package_id in package_ids:
            yield self.registry._get_package_name(package_id)

    def get_package_count(self) -> int:
        """
        Returns the number of packages available on the current registry.
        """
        self._validate_set_registry()
        return self.registry._num_package_ids()

    def get_release_count(self, package_name: str) -> int:
        """
        Returns the number of releases of the given package name available on the current registry.
        """
        validate_package_name(package_name)
        self._validate_set_registry()
        return self.registry._num_release_ids(package_name)

    def get_release_id(self, package_name: str, version: str) -> bytes:
        """
        Returns the 32 byte identifier of a release for the given package name and version,
        if they are available on the current registry.
        """
        validate_package_name(package_name)
        validate_package_version(version)
        self._validate_set_registry()
        return self.registry._get_release_id(package_name, version)

    @to_tuple
    def get_all_package_releases(self, package_name: str) -> Iterable[Tuple[str, str]]:
        """
        Returns a tuple of release data (version, manifest_ur) for every release of the
        given package name available on the current registry.
        """
        validate_package_name(package_name)
        self._validate_set_registry()
        release_ids = self.registry._get_all_release_ids(package_name)
        for release_id in release_ids:
            _, version, manifest_uri = self.registry._get_release_data(release_id)
            yield (version, manifest_uri)

    def get_release_id_data(self, release_id: bytes) -> Tuple[str, str, str]:
        """
        Returns ``(package_name, version, manifest_uri)`` associated with the given
        release id, *if* it is available on the current registry.

        * Parameters:
            * ``release_id``: 32 byte release identifier
        """
        self._validate_set_registry()
        return self.registry._get_release_data(release_id)

    def get_release_data(self, package_name: str, version: str) -> Tuple[str, str, str]:
        """
        Returns ``(package_name, version, manifest_uri)`` associated with the given
        package name and version, *if* they are published to the currently set registry.

        * Parameters:
            * ``name``: Must be a valid package name.
            * ``version``: Must be a valid package version.
        """
        validate_package_name(package_name)
        validate_package_version(version)
        self._validate_set_registry()
        release_id = self.registry._get_release_id(package_name, version)
        return self.get_release_id_data(release_id)

    def get_package(self, package_name: str, version: str) -> Package:
        """
        Returns a ``Package`` instance, generated by the ``manifest_uri`` associated with the
        given package name and version, if they are published to the currently set registry.

        * Parameters:
            * ``name``: Must be a valid package name.
            * ``version``: Must be a valid package version.
        """
        validate_package_name(package_name)
        validate_package_version(version)
        self._validate_set_registry()
        _, _, release_uri = self.get_release_data(package_name, version)
        return self.get_package_from_uri(release_uri)

    def _validate_set_registry(self) -> None:
        try:
            self.registry
        except AttributeError:
            raise PMError(
                "web3fsnpy.pm does not have a set registry. "
                "Please set registry with either: "
                "web3fsnpy.pm.set_registry(address) or "
                "web3fsnpy.pm.deploy_and_set_registry()"
            )
        if not isinstance(self.registry, ERC1319Registry):
            raise PMError(
                "web3fsnpy.pm requires an instance of a subclass of ERC1319Registry "
                "to be set as the web3fsnpy.pm.registry attribute. Instead found: "
                f"{type(self.registry)}."
            )

    def _validate_set_ens(self) -> None:
        if not self.web3fsnpy:
            raise InvalidAddress(
                "Could not look up ENS address because no web3fsnpy " "connection available"
            )
        elif not self.web3fsnpy.ens:
            raise InvalidAddress(
                "Could not look up ENS address because web3fsnpy.ens is " "set to None"
            )


def get_simple_registry_manifest() -> Dict[str, Any]:
    return json.loads((ASSETS_DIR / "simple-registry" / "2.0.0a1.json").read_text())


def validate_is_supported_manifest_uri(uri):
    if not is_supported_content_addressed_uri(uri):
        raise ManifestValidationError(
            f"URI: {uri} is not a valid content-addressed URI. "
            "Currently only IPFS and Github content-addressed URIs are supported."
        )