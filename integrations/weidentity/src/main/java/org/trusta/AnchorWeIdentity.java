package org.trusta;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.webank.weid.contract.v2.WeIdContract;
import com.webank.weid.protocol.base.WeIdPrivateKey;
import com.webank.weid.protocol.base.WeIdPublicKey;
import com.webank.weid.protocol.request.ServiceArgs;
import com.webank.weid.protocol.response.ResponseData;
import com.webank.weid.service.BaseService;
import com.webank.weid.service.impl.WeIdServiceImpl;
import com.webank.weid.service.impl.engine.fiscov2.WeIdServiceEngineV2;
import com.webank.weid.protocol.base.WeIdDocument;
import com.webank.weid.protocol.base.ServiceProperty;
import com.webank.weid.util.WeIdUtils;
import org.fisco.bcos.sdk.client.Client;
import org.fisco.bcos.sdk.crypto.keypair.CryptoKeyPair;
import org.fisco.bcos.sdk.model.TransactionReceipt;
import org.fisco.bcos.sdk.transaction.manager.TransactionProcessorFactory;
import org.fisco.bcos.sdk.transaction.manager.AssembleTransactionProcessor;
import org.fisco.bcos.sdk.transaction.model.dto.TransactionResponse;
import org.fisco.bcos.sdk.contract.Contract;
import org.fisco.bcos.sdk.abi.datatypes.Function;
import org.fisco.bcos.sdk.abi.datatypes.Type;
import org.fisco.bcos.sdk.abi.datatypes.generated.Bytes32;
import org.fisco.bcos.sdk.abi.datatypes.generated.Uint256;
import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.SecureRandom;
import java.util.*;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/** Official SDK bridge for the isolated chain 101 ONLY. No wallet import API. */
public final class AnchorWeIdentity {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final SecureRandom RANDOM = new SecureRandom();
    private static Path root;
    private static boolean healthOnly;
    private static final class RoundContract extends Contract {
        RoundContract(String bin,String address,Client client,CryptoKeyPair pair) { super(bin,address,client,pair); }
        TransactionReceipt commit(List<Type> values) {
            return executeTransaction(new Function("commit",values,Collections.emptyList()));
        }
    }

    private static void protect(Path path, boolean directory) throws Exception {
        Files.setPosixFilePermissions(path, PosixFilePermissions.fromString(directory ? "rwx------" : "rw-------"));
    }
    private static byte[] random(int length) { byte[] b = new byte[length]; RANDOM.nextBytes(b); return b; }
    private static String key(String name, Client client, boolean create) throws Exception {
        if (!name.matches("[a-z][a-z0-9-]{0,40}")) throw new IllegalArgumentException("key name");
        Path master = root.resolve("secrets/master.aes");
        Path file = root.resolve("secrets/"+name+".enc");
        if (!Files.exists(master)) {
            if (!create) throw new IllegalStateException("missing master");
            Files.createDirectories(master.getParent()); protect(master.getParent(), true);
            Files.write(master, random(32), StandardOpenOption.CREATE_NEW); protect(master, false);
        }
        byte[] secret = Files.readAllBytes(master);
        if (secret.length != 32) throw new IllegalStateException("master damaged");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        String value;
        if (Files.exists(file)) {
            byte[] raw = Files.readAllBytes(file);
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(secret,"AES"), new GCMParameterSpec(128, Arrays.copyOf(raw,12)));
            cipher.updateAAD(name.getBytes(StandardCharsets.US_ASCII));
            value = new String(cipher.doFinal(Arrays.copyOfRange(raw,12,raw.length)), StandardCharsets.US_ASCII);
        } else {
            if (!create) throw new IllegalStateException("missing key");
            CryptoKeyPair generated = client.getCryptoSuite().createKeyPair();
            value = new BigInteger(generated.getHexPrivateKey(),16).toString(10);
            byte[] nonce = random(12);
            cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(secret,"AES"), new GCMParameterSpec(128,nonce));
            cipher.updateAAD(name.getBytes(StandardCharsets.US_ASCII));
            byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.US_ASCII));
            byte[] raw = ByteBuffer.allocate(12+encrypted.length).put(nonce).put(encrypted).array();
            try (FileChannel channel = FileChannel.open(file, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
                protect(file,false); channel.write(ByteBuffer.wrap(raw)); channel.force(true);
            }
        }
        Arrays.fill(secret,(byte)0);
        return value;
    }
    private static Object checked(ResponseData<?> response) {
        if (response.getErrorCode() != 0 || response.getResult() == null) throw new IllegalStateException("WeIdentity operation refused: "+response.getErrorCode());
        return response.getResult();
    }
    private static String contractAddress() throws Exception {
        return JSON.readTree(root.resolve("identity-contract.json").toFile()).get("address").asText();
    }
    private static Map<String,Object> run(JsonNode req) throws Exception {
        Client client = (Client)BaseService.getClient();
        String chain = client.getNodeVersion().getNodeVersion().getChainId();
        if (!"101".equals(chain) || client.getGroupId() != 1 || client.getCryptoType() != 0) throw new IllegalStateException("WRONG_CHAIN_OR_CRYPTO");
        String op = req.path("operation").asText();
        if (op.equals("health")) return Map.of("chain_id",101,"group_id",1,"sdk","weid-java-sdk-1.8.6-rc1","authority","OFFICIAL_SDK_REAL_FISCO_TESTNET");
        if (op.equals("anchor-deploy") || op.equals("anchor-commit")) {
            Path saved=root.resolve("round-contract.json");
            if (op.equals("anchor-deploy") && Files.exists(saved)) return JSON.convertValue(JSON.readTree(saved.toFile()),Map.class);
            JsonNode artifact=JSON.readTree(root.resolve("conf/roundanchor-artifact.json").toFile());
            String privateValue=key("round-publisher",client,op.equals("anchor-deploy"));
            CryptoKeyPair pair=client.getCryptoSuite().getKeyPairFactory().createKeyPair(new BigInteger(privateValue));
            AssembleTransactionProcessor processor=TransactionProcessorFactory.createAssembleTransactionProcessor(client,pair);
            TransactionResponse response=null;
            TransactionReceipt receipt;
            if (op.equals("anchor-deploy")) {
                response=processor.deployAndGetResponse(artifact.get("abi").toString(),artifact.get("bytecode").asText(),Collections.emptyList());
                receipt=response.getTransactionReceipt();
            } else {
                String address=JSON.readTree(saved.toFile()).get("address").asText();
                List<Type> params=new ArrayList<>();
                for (String field:List.of("round_id_hash","record_hash","evidence_root","snapshot_hash","verdict_hash")) {
                    String value=req.path(field).asText();
                    if (!value.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("commit hash");
                    params.add(new Bytes32(org.bouncycastle.util.encoders.Hex.decode(value)));
                }
                int count=req.path("count").asInt(-1), quorum=req.path("quorum").asInt(-1);
                if (count<0 || count>6 || quorum<1 || quorum>6) throw new IllegalArgumentException("membership");
                params.add(new Uint256(BigInteger.valueOf(count)));params.add(new Uint256(BigInteger.valueOf(quorum)));
                receipt=new RoundContract(artifact.get("bytecode").asText(),address,client,pair).commit(params);
            }
            if (receipt==null || !receipt.isStatusOK()) throw new IllegalStateException("ANCHOR_TRANSACTION_FAILED");
            Map<String,Object> result=new LinkedHashMap<>();
            result.put("chain_id",101);result.put("group_id",1);result.put("receipt",receipt);
            result.put("publisher",pair.getAddress());result.put("address",op.equals("anchor-deploy")?response.getContractAddress():JSON.readTree(saved.toFile()).get("address").asText());
            if (op.equals("anchor-deploy")) Files.write(saved,JSON.writeValueAsBytes(result),StandardOpenOption.CREATE_NEW);
            else {
                Path receipts=root.resolve("anchor-receipts");Files.createDirectories(receipts);
                Path target=receipts.resolve(req.get("round_id_hash").asText()+".json");
                // Preserve the first successful receipt even if stdout/IPC is lost.
                if (!Files.exists(target)) {
                    try (FileChannel channel=FileChannel.open(target,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)) {
                        channel.write(ByteBuffer.wrap(JSON.writeValueAsBytes(result)));channel.force(true);
                    }
                }
            }
            return result;
        }
        if (op.equals("deploy")) {
            Path saved = root.resolve("identity-contract.json");
            if (Files.exists(saved)) return JSON.convertValue(JSON.readTree(saved.toFile()), Map.class);
            String privateValue = key("identity-deployer",client,true);
            CryptoKeyPair pair = client.getCryptoSuite().getKeyPairFactory().createKeyPair(new BigInteger(privateValue));
            WeIdContract contract = WeIdContract.deploy(client,pair);
            TransactionReceipt receipt = contract.getDeployReceipt();
            if (receipt == null || !receipt.isStatusOK()) throw new IllegalStateException("DEPLOY_FAILED");
            Map<String,Object> result = new LinkedHashMap<>();
            result.put("address",contract.getContractAddress()); result.put("receipt",receipt);
            result.put("chain_id",101); result.put("sdk","weid-java-sdk-1.8.6-rc1");
            Files.write(saved,JSON.writeValueAsBytes(result),StandardOpenOption.CREATE_NEW);
            return result;
        }
        String agent = req.path("agent").asText();
        String privateValue = key(agent,client,op.equals("register"));
        CryptoKeyPair pair = client.getCryptoSuite().getKeyPairFactory().createKeyPair(new BigInteger(privateValue));
        String pub = new BigInteger(pair.getHexPublicKey(),16).toString(10);
        String did = WeIdUtils.convertPublicKeyToWeId(pub);
        WeIdPrivateKey privateKey = new WeIdPrivateKey(); privateKey.setPrivateKey(privateValue);
        // Use the official identity engine directly: the high-level SDK eagerly
        // initializes unrelated issuer/CPT contracts. No fake addresses are used
        // to satisfy that unrelated full-suite preflight.
        WeIdServiceEngineV2 service = new WeIdServiceEngineV2();
        Map<String,Object> result = new LinkedHashMap<>();
        if (op.equals("register")) {
            if (!Boolean.TRUE.equals(checked(service.isWeIdExist(did)))) {
                ResponseData<Boolean> response = service.createWeId(pair.getAddress(),pub,privateValue);
                if (!Boolean.TRUE.equals(checked(response))) throw new IllegalStateException("DID_MISMATCH");
                result.put("transaction_info",response.getTransactionInfo());
            }
        } else if (op.equals("bind")) {
            String hash = req.path("binding_hash").asText();
            int epoch = req.path("epoch").asInt(-1);
            if (!hash.matches("[0-9a-f]{64}") || epoch < 0) throw new IllegalArgumentException("binding");
            ServiceProperty args = new ServiceProperty();
            args.setId(did+"#trusta-p256-"+epoch); args.setType("TRUSTA-P256-Binding");
            args.setServiceEndpoint("urn:trusta:anchor:binding:"+hash);
            WeIdDocument document = (WeIdDocument)checked(service.getWeIdDocument(did));
            document.getService().removeIf(item -> item.getId().equals(args.getId()));
            document.getService().add(args);
            ResponseData<Boolean> response = service.updateWeId(document,pair.getAddress(),privateValue);
            if (!Boolean.TRUE.equals(checked(response))) throw new IllegalStateException("BIND_FAILED");
            result.put("transaction_info",response.getTransactionInfo());
        } else if (op.equals("deactivate") || op.equals("reactivate")) {
            WeIdContract contract = WeIdContract.load(contractAddress(),client,pair);
            TransactionReceipt receipt = contract.deactivateWeId(pair.getAddress(),op.equals("deactivate"));
            if (!receipt.isStatusOK()) throw new IllegalStateException("DEACTIVATION_FAILED");
            result.put("receipt",receipt);
        } else if (!op.equals("query")) throw new IllegalArgumentException("operation");
        result.put("did",did); result.put("chain_id",101); result.put("contract_address",contractAddress());
        result.put("document",checked(service.getWeIdDocument(did)));
        result.put("deactivated",checked(service.isDeactivated(did)));
        result.put("block_number",client.getBlockNumber().getBlockNumber().toString());
        return result;
    }
    public static void main(String[] args) {
        try {
            if (args.length != 1) throw new IllegalArgumentException();
            root = Paths.get(args[0]).toRealPath();
            if (!root.endsWith("weidentity-lab")) throw new IllegalArgumentException();
            byte[] raw = System.in.readNBytes(16385);
            if (raw.length > 16384) throw new IllegalArgumentException();
            JsonNode req = JSON.readTree(raw);
            healthOnly = "health".equals(req.path("operation").asText()) || req.path("operation").asText().startsWith("anchor-");
            try (FileChannel channel = FileChannel.open(root.resolve("operator.lock"),StandardOpenOption.CREATE,StandardOpenOption.WRITE);
                 var lock = channel.lock()) {
                System.out.println("TRUSTA_JSON="+JSON.writeValueAsString(run(req)));
            }
            System.exit(0);
        } catch (Throwable e) {
            if (healthOnly) {
                Throwable cause = e;
                for (int i=0; i<8 && cause.getCause()!=null; i++) cause=cause.getCause();
                String message = String.valueOf(cause.getMessage()).replaceAll("[0-9a-fA-F]{40,}","[REDACTED]");
                try { System.out.println("TRUSTA_DIAGNOSTIC="+JSON.writeValueAsString(message.substring(0,Math.min(message.length(),200)))); }
                catch (Exception ignored) { }
            }
            // No exception text, stack or SDK result object with private material.
            System.out.println("TRUSTA_JSON={\"error\":\"WEIDENTITY_OPERATION_FAILED\",\"type\":\""+e.getClass().getSimpleName()+"\"}");
            System.exit(2);
        }
    }
}
