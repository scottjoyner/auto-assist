package b01research;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.Signature;
import java.security.SecureRandom;
import java.net.URL;
import java.net.Proxy;
import java.net.HttpURLConnection;
import java.io.OutputStream;
import java.io.InputStream;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.stream.Stream;
import java.util.regex.Pattern;
import org.neo4j.graphdb.Label;
import org.neo4j.graphdb.Lock;
import org.neo4j.graphdb.Node;
import org.neo4j.graphdb.Transaction;
import org.neo4j.procedure.Context;
import org.neo4j.procedure.Mode;
import org.neo4j.procedure.Name;
import org.neo4j.procedure.Procedure;

/** Lab ONLY: pinned public roots in disposable Neo4j process environment. */
public final class FixtureSignedClaim {
  @Context public Transaction tx;
  private static final Pattern TASK = Pattern.compile("fixture-task-[A-Za-z0-9_-]{4,48}");
  private static final Pattern AGENT = Pattern.compile("fixture-agent-[A-Za-z0-9_-]{4,48}");
  private static final Pattern NONCE = Pattern.compile("nonce-[A-Za-z0-9_-]{6,48}");
  private static final Pattern SHA = Pattern.compile("[0-9a-f]{64}");
  private static final Pattern SIGNATURE_HEX = Pattern.compile("[0-9a-f]{128}");
  private static final String SCOPE="fixture-assistx-b01";
  private static final String OP="claim_fixture_task";
  private static final byte[] ASN_ED25519_PREFIX=HexFormat.of().parseHex(
      "302a300506032b6570032100");

  public static final class Receipt {
    public String result;
    public long term;
    public long highWater;
    public Receipt(String result,long term,long highWater) {
      this.result=result;this.term=term;this.highWater=highWater;
    }
  }

  private static RuntimeException denied() {
    return new IllegalArgumentException("SYNTHETIC_ADMISSION_DENIED");
  }
  private static boolean accepted(Pattern pat,String input) {
    return input!=null && pat.matcher(input).matches();
  }
  private static byte[] envRoot(String key) throws Exception {
    String raw=System.getenv(key);
    if (!accepted(SHA,raw)) throw denied();
    byte[] out=HexFormat.of().parseHex(raw);
    if(out.length!=32) throw denied();
    return out;
  }
  private static void verify(byte[] root, String proof,byte[] message)
      throws Exception {
    if(!accepted(SIGNATURE_HEX,proof)) throw denied();
    byte[] der=Arrays.copyOf(ASN_ED25519_PREFIX,ASN_ED25519_PREFIX.length+32);
    System.arraycopy(root,0,der,ASN_ED25519_PREFIX.length,32);
    KeyFactory kf=KeyFactory.getInstance("Ed25519");
    Signature verifier=Signature.getInstance("Ed25519");
    verifier.initVerify(kf.generatePublic(new X509EncodedKeySpec(der)));
    verifier.update(message);
    if(!verifier.verify(HexFormat.of().parseHex(proof))) throw denied();
  }
  private static byte[] message(String instance,String task,String agent,
      long term,long sequence,String nonce,String policy,long issued,long expiry) {
    // Fixed fields with no newlines, canonical ASCII.
    String body=String.join("\n","B01-NATIVE-CLAIM-v0",instance,SCOPE,OP,
        task,agent,Long.toString(term),Long.toString(sequence),
        nonce,policy,Long.toString(issued),Long.toString(expiry));
    return body.getBytes(StandardCharsets.US_ASCII);
  }
  private static void requireWitness(String issuerSig, String deviceSig,
      byte[] originalSignedMessage) throws Exception {
    // Pinned exact fixture destination, never client-provided endpoint.
    String url=System.getenv("B01_FIXTURE_WITNESS_URL");
    if(!"http://b01-witness:8812/reserve".equals(url)) throw denied();
    String bearer=System.getenv("B01_FIXTURE_WITNESS_BEARER");
    if (bearer==null || bearer.length()<30 || bearer.length()>200) throw denied();
    byte[] challenge=new byte[32];
    new SecureRandom().nextBytes(challenge);
    String raw=new String(originalSignedMessage,StandardCharsets.US_ASCII)
        +"\n"+issuerSig+"\n"+deviceSig+"\n"
        +HexFormat.of().formatHex(challenge);
    byte[] request=raw.getBytes(StandardCharsets.US_ASCII);
    HttpURLConnection link=(HttpURLConnection)new URL(url).openConnection(Proxy.NO_PROXY);
    link.setConnectTimeout(2500);
    link.setReadTimeout(4000);
    link.setInstanceFollowRedirects(false);
    link.setUseCaches(false);
    link.setRequestMethod("POST");
    link.setRequestProperty("X-Fixture-Graph-Bearer",bearer);
    link.setRequestProperty("Content-Type","text/plain");
    link.setDoOutput(true);
    int status;
    String reply;
    try {
      try(OutputStream out=link.getOutputStream()) {
        out.write(request);
      }
      status=link.getResponseCode();
      if(status!=200) throw denied();
      try(InputStream in=link.getInputStream()) {
        reply=new String(in.readNBytes(180),StandardCharsets.US_ASCII);
      }
    } finally {
      link.disconnect();
    }
    if(reply.length()!=136 || !reply.startsWith("PENDING\n")) throw denied();
    byte[] receiptMessage=MessageDigest.getInstance("SHA-256").digest(request);
    byte[] domain="B01-WITNESS-PENDING-v0\n".getBytes(StandardCharsets.US_ASCII);
    byte[] signedReceipt=Arrays.copyOf(domain,domain.length+receiptMessage.length);
    System.arraycopy(receiptMessage,0,signedReceipt,domain.length,receiptMessage.length);
    // The witness signs this request's random fresh-challenge digest. An
    // earlier HTTP 200/receipt cannot authorize a new attempt.
    verify(envRoot("B01_FIXTURE_WITNESS_ROOT"),
        reply.substring(8),signedReceipt);
  }

  @Procedure(name="b01research.claimFixture",mode=Mode.WRITE)
  public Stream<Receipt> claim(
      @Name("instance") String instance,
      @Name("task_id") String taskId,
      @Name("agent_id") String agentId,
      @Name("term") long term,
      @Name("sequence") long sequence,
      @Name("nonce") String nonce,
      @Name("policy_sha256") String policy,
      @Name("issued_at") long issuedAt,
      @Name("expires_at") long expiresAt,
      @Name("issuer_sig_hex") String issuerSig,
      @Name("device_sig_hex") String deviceSig) {
    if (!accepted(SHA,instance) || !accepted(TASK,taskId) ||
        !accepted(AGENT,agentId) || !accepted(NONCE,nonce) ||
        !accepted(SHA,policy) || term<1 || sequence<1 ||
        expiresAt<=issuedAt || expiresAt-issuedAt>60) throw denied();
    long now=Instant.now().getEpochSecond();
    if (issuedAt>now || expiresAt<=now) throw denied();
    try {
      byte[] issuer=envRoot("B01_FIXTURE_ISSUER_ROOT");
      byte[] device=envRoot("B01_FIXTURE_DEVICE_ROOT");
      if(MessageDigest.isEqual(issuer,device)) throw denied();
      byte[] bytes=message(instance,taskId,agentId,term,sequence,
          nonce,policy,issuedAt,expiresAt);
      verify(issuer,issuerSig,bytes);
      verify(device,deviceSig,bytes);
      Node guard=tx.findNode(Label.label("FixtureFence"),"scope",SCOPE);
      if(guard==null) throw denied();
      // Serialize all accepted contenders within caller's transaction.
      try (Lock lock=tx.acquireWriteLock(guard)) {
        Node task=tx.findNode(Label.label("FixtureTask"),"id",taskId);
        if(task==null) throw denied();
        if(!instance.equals(guard.getProperty("instance","")) ||
           !policy.equals(guard.getProperty("policy_sha256","")) ||
           !agentId.equals(task.getProperty("owner_id","")) ||
           ((Number)guard.getProperty("term",0)).longValue()!=term ||
           ((Number)guard.getProperty("high_water",-1)).longValue()+1!=sequence ||
           !"READY".equals(task.getProperty("status","")) ||
           (Boolean.TRUE.equals(task.getProperty("requires_recovery_identity",false))
               && !agentId.equals(task.getProperty("recovery_agent_id",""))))
           throw denied();
        Object seenValue=guard.getProperty("seen_nonces",new String[0]);
        if(!(seenValue instanceof String[])) throw denied();
        String[] seen=(String[])seenValue;
        if(Arrays.asList(seen).contains(nonce)) throw denied();
        // Persist a remote PENDING witness *before* the Neo4j write. If the
        // remote service fails after reservation, graph TX rolls back and
        // the remote witness remains pending: never replay automatically.
        requireWitness(issuerSig,deviceSig,bytes);
        String[] next=Arrays.copyOf(seen,seen.length+1);
        next[seen.length]=nonce;
        guard.setProperty("high_water",sequence);
        guard.setProperty("seen_nonces",next);
        task.setProperty("status","CLAIMED");
        task.setProperty("claimed_by",agentId);
        task.setProperty("claim_nonce",nonce);
        task.setProperty("claim_sequence",sequence);
        return Stream.of(new Receipt("FIXTURE_CLAIMED",term,sequence));
      }
    } catch (Exception exc) {
      // Deny without disclosing witness response, network, proof, or keys.
      throw denied();
    }
  }
}