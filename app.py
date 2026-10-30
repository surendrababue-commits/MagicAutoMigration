import xml.etree.ElementTree as ET
import json
import uuid
import base64
import streamlit as st

def convert_magic_to_custom_final(uploaded_file):
    tree = ET.parse(uploaded_file)
    root = tree.getroot()
    
    src_wrappers = {}
    for field in root.findall('.//SourceSchemas//Field'):
        xsi_type = field.attrib.get('{http://www.w3.org/2001/XMLSchema-instance}type', '')
        if xsi_type == 'JsonArrayComplexField':
            parent_id = field.find('Identifier').text
            for child in root.findall('.//SourceSchemas//Field'):
                child_id = child.find('Identifier').text
                max_inst = child.find('MaxInstance').text if child.find('MaxInstance') is not None else '0'
                if child_id and child_id.startswith(parent_id + '/') and child_id.count('/') == parent_id.count('/') + 1:
                    if max_inst not in ('0', '1'):
                        src_wrappers[child_id] = parent_id

    sorted_src_wrappers = sorted(src_wrappers.items(), key=lambda x: len(x[0]), reverse=True)
    src_arrays = set(parent for wrapper, parent in sorted_src_wrappers)

    dest_arrays = set()
    primitive_dest_arrays = set()
    dest_fields = root.findall('.//DestinationSchemas//Field')
    
    for field in dest_fields:
        ident = field.find('Identifier')
        if ident is None or not ident.text:
            continue
            
        dest_id = ident.text
        max_inst = field.find('MaxInstance')
        if max_inst is not None and max_inst.text not in ('0', '1'):
            dest_arrays.add(dest_id)
            
        is_multi_simple = field.find('IsMultiSimple')
        if is_multi_simple is not None and is_multi_simple.text == 'true':
            primitive_dest_arrays.add(dest_id)

    connections = root.find('.//Connections')
    dest_to_source = {}
    if connections is not None:
        for conn in connections.findall('ConnectionData'):
            dest_to_source[conn.find('DestinationIdentifier').text] = conn.find('SourceIdentifier').text

    custom_mapping = {
        "sourceSettings": {
            "format": "json-schema",
            "title": "Source",
            "advanced": {"delimiter": ",", "textQualifier": "\"", "includeHeader": True}
        },
        "destinationSettings": {
            "format": "xsd",
            "title": "Destination",
            "advanced": {"delimiter": ",", "textQualifier": "\"", "includeHeader": True}
        },
        "mappings": []
    }

    def star_path(path, arrays):
        p = path
        for arr in sorted(list(arrays), key=len, reverse=True):
            p = p.replace(arr + '/', arr + '[*]/')
            if p == arr:
                p = arr + '[*]'
        return p

    def star_parent_path(path, arrays):
        p = path
        for arr in sorted(list(arrays), key=len, reverse=True):
            if p.startswith(arr + '/'):
                p = p.replace(arr + '/', arr + '[*]/')
        return p

    for field in dest_fields:
        dest_id_node = field.find('Identifier')
        if dest_id_node is None or not dest_id_node.text:
            continue
            
        dest_id = dest_id_node.text
        if dest_id == 'ADT_A30' or dest_id == 'root':
            continue

        if dest_id in dest_to_source:
            src_id = dest_to_source[dest_id]
            if src_id == 'root':
                continue
            
            clean_src = src_id
            for wrapper, parent in sorted_src_wrappers:
                if clean_src == wrapper:
                    clean_src = parent
                clean_src = clean_src.replace(wrapper + '/Value', parent)
                clean_src = clean_src.replace(wrapper + '/', parent + '/')
            
            clean_dst = dest_id
            if '~Value' in clean_dst:
                clean_dst = clean_dst.rsplit('/', 1)[0]

            is_primitive_base = dest_id in primitive_dest_arrays
            is_primitive_val = '~Value' in dest_id
            is_complex_base = (dest_id in dest_arrays and not is_primitive_base and not is_primitive_val)

            if is_complex_base:
                final_src = clean_src.replace('/', '.')
                final_dst = clean_dst
                data_type, map_type, transform = "array", "auto", {"type": "DIRECT"}
            elif is_primitive_base:
                final_src = star_parent_path(clean_src, src_arrays).replace('/', '.')
                final_dst = star_parent_path(clean_dst, dest_arrays)
                data_type, map_type, transform = "array", "manual", {"type": "DIRECT", "params": {}}
            else:
                final_src = star_path(clean_src, src_arrays).replace('/', '.')
                final_dst = star_path(clean_dst, dest_arrays)
                data_type, map_type, transform = "string", "manual", {"type": "DIRECT", "params": {}}

            custom_mapping["mappings"].append({
                "id": str(uuid.uuid4()),
                "sources": [{"path": final_src, "type": data_type}],
                "destination": {"path": final_dst, "type": data_type},
                "transform": transform,
                "type": map_type
            })
            
        else:
            expr_node = field.find('.//Expression/ExpressionVal')
            if expr_node is not None and expr_node.text:
                try:
                    decoded_expr = base64.b64decode(expr_node.text).decode('utf-8')
                    if decoded_expr.startswith("'") and decoded_expr.endswith("'"):
                        decoded_expr = decoded_expr[1:-1]
                except Exception:
                    decoded_expr = expr_node.text
                
                final_dst = star_path(dest_id, dest_arrays)
                custom_mapping["mappings"].append({
                    "id": str(uuid.uuid4()),
                    "sources": [],
                    "destination": {"path": final_dst, "type": "string"},
                    "transform": {"type": "EXPRESSION"},
                    "expression": decoded_expr,
                    "type": "expression"
                })

    return json.dumps(custom_mapping, indent=2)

# UI Configuration
st.set_page_config(page_title="Magic xpi to JSON Converter")
st.title("Magic xpi to JSON Schema Converter")
st.markdown("Upload your Magic xpi mapping XML to automatically generate the new JSON schema.")

uploaded_file = st.file_uploader("Upload Magic xpi XML/TXT file", type=['xml', 'txt'])

if uploaded_file is not None:
    if st.button("Convert File"):
        with st.spinner("Converting..."):
            try:
                json_output = convert_magic_to_custom_final(uploaded_file)
                st.success("Conversion complete!")
                
                st.download_button(
                    label="Download Converted JSON",
                    data=json_output,
                    file_name="Converted_Mapping.json",
                    mime="application/json"
                )
            except Exception as e:
                st.error(f"An error occurred during conversion: {str(e)}")